using System.Buffers.Binary;
using System.Text;

namespace IRacingCoach.Telemetry;

/// <summary>
/// Reads iRacing <c>.ibt</c> telemetry the way the deterministic Python backend
/// does, so this managed decoder can replace <c>ibt_reader.load_telemetry</c>'s
/// hot path without changing a single decoded value.
///
/// Decode is the largest and hottest slice of the backend - roughly 86% of a
/// race's analysis time and the reason the recorder was throttled to 20 Hz - and
/// it carries no evidence or coaching judgement: it is pure binary parsing. That
/// makes it the correct first module to move to C#, and it is guarded by a
/// differential parity test that asserts this reader is byte-identical to the
/// Python oracle on real recordings. Nothing here may drift from
/// <c>iracing-coach/skills/analyze-iracing-race/scripts/ibt_reader.py</c>
/// without the golden being regenerated and reviewed.
/// </summary>
public sealed class IbtDecoder
{
    // irsdk_header: 28 little-endian int32 values (112 bytes), then the disk
    // subheader qddii. Field positions mirror ibt_reader._read_layout exactly.
    private const int HeaderInts = 28;
    private const int HeaderSize = HeaderInts * 4;
    private const int DiskSubheaderSize = 8 + 8 + 8 + 4 + 4; // q d d i i
    private const int VarHeaderSize = 4 + 4 + 4 + 1 + 3 + 32 + 64 + 32; // 144
    private const int MaxTickRate = 10_000;
    private const int MaxVariables = 16_384;
    private const long MaxSessionInfoBytes = 64L * 1024 * 1024;

    public static IbtDecodeResult Load(string path, double? targetHz = 20)
    {
        ArgumentNullException.ThrowIfNull(path);
        byte[] bytes = File.ReadAllBytes(path);
        return Load(bytes, path, targetHz);
    }

    public static IbtDecodeResult Load(ReadOnlySpan<byte> file, string path, double? targetHz)
    {
        if (file.Length < HeaderSize + DiskSubheaderSize)
        {
            throw new IbtFormatException($"{Label(path)}: file is smaller than the fixed IBT header.");
        }

        ReadOnlySpan<byte> headerBytes = file[..HeaderSize];
        int version = HeaderInt(headerBytes, 0);
        int tickRate = HeaderInt(headerBytes, 2);
        int sessionInfoLen = HeaderInt(headerBytes, 4);
        int sessionInfoOffset = HeaderInt(headerBytes, 5);
        int numVars = HeaderInt(headerBytes, 6);
        int varHeaderOffset = HeaderInt(headerBytes, 7);
        int numBuf = HeaderInt(headerBytes, 8);
        int bufferLength = HeaderInt(headerBytes, 9);
        // varBuf[0] occupies ints 12 (tick_count) and 13 (buffer_offset).
        int bufferOffset = HeaderInt(headerBytes, 13);

        if (version <= 0 || version > 1_000)
        {
            throw new IbtFormatException($"{Label(path)}: implausible SDK header version {version}.");
        }
        if (tickRate <= 0 || tickRate > MaxTickRate)
        {
            throw new IbtFormatException($"{Label(path)}: invalid tick rate {tickRate} Hz.");
        }
        if (sessionInfoLen < 0 || sessionInfoLen > MaxSessionInfoBytes)
        {
            throw new IbtBoundsException($"{Label(path)}: invalid session info length {sessionInfoLen}.");
        }
        if (numVars < 0 || numVars > MaxVariables)
        {
            throw new IbtBoundsException($"{Label(path)}: invalid variable count {numVars}.");
        }
        if (numBuf < 1 || numBuf > 4)
        {
            throw new IbtBoundsException($"{Label(path)}: invalid telemetry buffer count {numBuf}.");
        }
        if (bufferLength <= 0)
        {
            throw new IbtBoundsException($"{Label(path)}: invalid sample buffer length {bufferLength}.");
        }
        if (bufferOffset < 0)
        {
            throw new IbtBoundsException($"{Label(path)}: negative telemetry buffer offset {bufferOffset}.");
        }

        // Disk subheader lives immediately after the 112-byte header; record
        // count is its fifth field (q d d i i -> index 4).
        int recordCount = BinaryPrimitives.ReadInt32LittleEndian(file.Slice(HeaderSize + 8 + 8 + 8 + 4, 4));
        if (recordCount < 0)
        {
            throw new IbtBoundsException($"{Label(path)}: negative disk record count {recordCount}.");
        }

        long variableRegionLen = (long)numVars * VarHeaderSize;
        if (varHeaderOffset < 0 || varHeaderOffset + variableRegionLen > file.Length)
        {
            throw new IbtBoundsException($"{Label(path)}: variable header region is outside the file.");
        }

        var variables = new List<IbtVariable>(numVars);
        var seen = new HashSet<string>(StringComparer.Ordinal);
        for (int i = 0; i < numVars; i++)
        {
            ReadOnlySpan<byte> header = file.Slice(varHeaderOffset + i * VarHeaderSize, VarHeaderSize);
            int typeCode = BinaryPrimitives.ReadInt32LittleEndian(header[..4]);
            int offset = BinaryPrimitives.ReadInt32LittleEndian(header.Slice(4, 4));
            int count = BinaryPrimitives.ReadInt32LittleEndian(header.Slice(8, 4));
            bool countAsTime = header[12] != 0;
            string name = DecodeCString(header.Slice(16, 32));
            string description = DecodeCString(header.Slice(48, 64));
            string unit = DecodeCString(header.Slice(112, 32));

            if (typeCode is < 0 or > 5)
            {
                throw new IbtTypeException($"{Label(path)}: variable #{i} {name} uses unsupported SDK type code {typeCode}.");
            }
            if (string.IsNullOrEmpty(name))
            {
                throw new IbtFormatException($"{Label(path)}: variable header #{i} has no name.");
            }
            if (!seen.Add(name))
            {
                throw new IbtFormatException($"{Label(path)}: duplicate variable name {name}.");
            }
            if (count <= 0)
            {
                throw new IbtBoundsException($"{Label(path)}: variable {name} has invalid element count {count}.");
            }

            var variable = new IbtVariable(typeCode, offset, count, countAsTime, name, description, unit);
            if (offset < 0 || offset + variable.ByteSize > bufferLength)
            {
                throw new IbtBoundsException(
                    $"{Label(path)}: variable {name} occupies bytes [{offset}, {offset + variable.ByteSize}) outside the {bufferLength}-byte sample buffer.");
            }
            variables.Add(variable);
        }

        long sampleBytes = (long)recordCount * bufferLength;
        if (sampleBytes > 0 && bufferOffset + sampleBytes > file.Length)
        {
            throw new IbtBoundsException($"{Label(path)}: telemetry sample buffers extend past the end of the file.");
        }

        (int[] indices, double outputRate) = SamplingPlan(recordCount, tickRate, targetHz);

        // Column-oriented, matching samples[channel][sampleIndex] in Python.
        var columns = new IbtColumn[variables.Count];
        for (int v = 0; v < variables.Count; v++)
        {
            columns[v] = IbtColumn.Allocate(variables[v], indices.Length);
        }

        for (int s = 0; s < indices.Length; s++)
        {
            long recordOffset = bufferOffset + (long)indices[s] * bufferLength;
            for (int v = 0; v < variables.Count; v++)
            {
                IbtVariable variable = variables[v];
                ReadOnlySpan<byte> cell = file.Slice((int)(recordOffset + variable.Offset), variable.ByteSize);
                columns[v].Decode(s, cell);
            }
        }

        return new IbtDecodeResult(
            variables,
            columns,
            indices,
            nativeTickRateHz: tickRate,
            sampleRateHz: outputRate,
            sourceRecordCount: recordCount,
            path: path);
    }

    /// <summary>
    /// Mirrors <c>ibt_reader._sampling_plan</c> + <c>_iter_sample_indices</c>: a
    /// null or above-native target keeps every record; otherwise a fractional
    /// stride is walked with <c>int(position)</c> truncation and adjacent
    /// duplicates dropped, which is what produces the exact 60->20 Hz decimation.
    /// </summary>
    internal static (int[] Indices, double OutputRate) SamplingPlan(int recordCount, int tickRate, double? targetHz)
    {
        if (recordCount <= 0)
        {
            return (Array.Empty<int>(), targetHz is { } t && t < tickRate ? t : tickRate);
        }

        double step;
        double outputRate;
        if (targetHz is not { } target)
        {
            step = 1.0;
            outputRate = tickRate;
        }
        else
        {
            if (double.IsNaN(target) || double.IsInfinity(target) || target <= 0)
            {
                throw new ArgumentException("targetHz must be a positive finite number or null.", nameof(targetHz));
            }
            if (target >= tickRate)
            {
                step = 1.0;
                outputRate = tickRate;
            }
            else
            {
                step = tickRate / target;
                outputRate = target;
            }
        }

        if (step <= 1.0)
        {
            var all = new int[recordCount];
            for (int i = 0; i < recordCount; i++)
            {
                all[i] = i;
            }
            return (all, outputRate);
        }

        var indices = new List<int>(recordCount);
        double position = 0.0;
        int previous = -1;
        while (true)
        {
            int index = (int)position; // truncation toward zero, matching Python int()
            if (index >= recordCount)
            {
                break;
            }
            if (index != previous)
            {
                indices.Add(index);
                previous = index;
            }
            position += step;
        }
        return (indices.ToArray(), outputRate);
    }

    /// <summary>
    /// Mirrors <c>ibt_reader._decode_c_string</c>: truncate at the first NUL,
    /// decode UTF-8, and fall back to Windows-1252 with replacement for the old
    /// driver/setup names that are not valid UTF-8.
    /// </summary>
    private static int HeaderInt(ReadOnlySpan<byte> header, int index)
        => BinaryPrimitives.ReadInt32LittleEndian(header.Slice(index * 4, 4));

    internal static string DecodeCString(ReadOnlySpan<byte> raw)
    {
        int nul = raw.IndexOf((byte)0);
        ReadOnlySpan<byte> content = nul >= 0 ? raw[..nul] : raw;
        if (content.IsEmpty)
        {
            return string.Empty;
        }

        try
        {
            return new UTF8Encoding(encoderShouldEmitUTF8Identifier: false, throwOnInvalidBytes: true).GetString(content);
        }
        catch (DecoderFallbackException)
        {
            return Encoding.GetEncoding(1252, EncoderFallback.ReplacementFallback, DecoderFallback.ReplacementFallback)
                .GetString(content);
        }
    }

    private static string Label(string path) => Path.GetFileName(path) is { Length: > 0 } name ? name : path;
}
