using System.Buffers.Binary;
using System.Security.Cryptography;
using System.Text;

namespace IRacingCoach.Telemetry;

/// <summary>SDK element type codes, matching ibt_reader._TYPE_INFO.</summary>
public enum IbtType
{
    Char = 0,
    Bool = 1,
    Int = 2,
    Bitfield = 3,
    Float = 4,
    Double = 5,
}

/// <summary>One decoded channel's header, mirroring ibt_reader._Variable.</summary>
public sealed record IbtVariable(
    int TypeCode,
    int Offset,
    int Count,
    bool CountAsTime,
    string Name,
    string Description,
    string Unit)
{
    public IbtType Type => (IbtType)TypeCode;

    public int ElementSize => TypeCode switch
    {
        0 => 1, // char
        1 => 1, // bool
        2 => 4, // int
        3 => 4, // bitfield
        4 => 4, // float
        5 => 8, // double
        _ => throw new IbtTypeException($"unsupported SDK type code {TypeCode}"),
    };

    public int ByteSize => ElementSize * Count;
}

/// <summary>
/// A column of decoded values for one channel across the retained samples.
/// Char channels hold decoded strings; every other channel holds one boxed
/// scalar per sample, or a list of boxed elements for an SDK array variable -
/// the same shape ibt_reader emits (samples[name][i] is scalar or list).
/// </summary>
public sealed class IbtColumn
{
    private readonly IbtVariable _variable;
    private readonly string[]? _strings;
    private readonly object[]? _values;

    private IbtColumn(IbtVariable variable, string[]? strings, object[]? values)
    {
        _variable = variable;
        _strings = strings;
        _values = values;
    }

    public IbtVariable Variable => _variable;
    public int Length => _strings?.Length ?? _values!.Length;

    public static IbtColumn Allocate(IbtVariable variable, int sampleCount)
        => variable.TypeCode == 0
            ? new IbtColumn(variable, new string[sampleCount], null)
            : new IbtColumn(variable, null, new object[sampleCount]);

    /// <summary>Decode one cell of raw bytes into slot <paramref name="sample"/>.</summary>
    public void Decode(int sample, ReadOnlySpan<byte> cell)
    {
        int count = _variable.Count;
        switch (_variable.TypeCode)
        {
            case 0: // char array -> string, NUL-truncated, UTF-8 / cp1252
                _strings![sample] = IbtDecoder.DecodeCString(cell);
                break;
            case 1: // bool
                if (count == 1)
                {
                    _values![sample] = cell[0] != 0;
                }
                else
                {
                    var list = new object[count];
                    for (int i = 0; i < count; i++)
                    {
                        list[i] = cell[i] != 0;
                    }
                    _values![sample] = list;
                }
                break;
            case 2: // int32
                if (count == 1)
                {
                    _values![sample] = BinaryPrimitives.ReadInt32LittleEndian(cell);
                }
                else
                {
                    var list = new object[count];
                    for (int i = 0; i < count; i++)
                    {
                        list[i] = BinaryPrimitives.ReadInt32LittleEndian(cell.Slice(i * 4, 4));
                    }
                    _values![sample] = list;
                }
                break;
            case 3: // bitfield -> uint32
                if (count == 1)
                {
                    _values![sample] = BinaryPrimitives.ReadUInt32LittleEndian(cell);
                }
                else
                {
                    var list = new object[count];
                    for (int i = 0; i < count; i++)
                    {
                        list[i] = BinaryPrimitives.ReadUInt32LittleEndian(cell.Slice(i * 4, 4));
                    }
                    _values![sample] = list;
                }
                break;
            case 4: // float32 -> double (widened, matching Python struct 'f')
                if (count == 1)
                {
                    _values![sample] = (double)BinaryPrimitives.ReadSingleLittleEndian(cell);
                }
                else
                {
                    var list = new object[count];
                    for (int i = 0; i < count; i++)
                    {
                        list[i] = (double)BinaryPrimitives.ReadSingleLittleEndian(cell.Slice(i * 4, 4));
                    }
                    _values![sample] = list;
                }
                break;
            case 5: // float64
                if (count == 1)
                {
                    _values![sample] = BinaryPrimitives.ReadDoubleLittleEndian(cell);
                }
                else
                {
                    var list = new object[count];
                    for (int i = 0; i < count; i++)
                    {
                        list[i] = BinaryPrimitives.ReadDoubleLittleEndian(cell.Slice(i * 8, 8));
                    }
                    _values![sample] = list;
                }
                break;
            default:
                throw new IbtTypeException($"unsupported SDK type code {_variable.TypeCode}");
        }
    }

    public string GetString(int sample) => _strings![sample];
    public object GetValue(int sample) => _values![sample];
}

/// <summary>The decoded telemetry, column-oriented like load_telemetry's output.</summary>
public sealed class IbtDecodeResult
{
    private readonly Dictionary<string, IbtColumn> _byName;

    public IbtDecodeResult(
        IReadOnlyList<IbtVariable> variables,
        IReadOnlyList<IbtColumn> columns,
        int[] sampleIndices,
        int nativeTickRateHz,
        double sampleRateHz,
        int sourceRecordCount,
        string path)
    {
        Variables = variables;
        Columns = columns;
        SampleIndices = sampleIndices;
        NativeTickRateHz = nativeTickRateHz;
        SampleRateHz = sampleRateHz;
        SourceRecordCount = sourceRecordCount;
        Path = path;
        _byName = new Dictionary<string, IbtColumn>(columns.Count, StringComparer.Ordinal);
        foreach (IbtColumn column in columns)
        {
            _byName[column.Variable.Name] = column;
        }
    }

    public IReadOnlyList<IbtVariable> Variables { get; }
    public IReadOnlyList<IbtColumn> Columns { get; }
    public int[] SampleIndices { get; }
    public int NativeTickRateHz { get; }
    public double SampleRateHz { get; }
    public int SourceRecordCount { get; }
    public int SampleCount => SampleIndices.Length;
    public string Path { get; }

    public IbtColumn Column(string name) => _byName[name];
    public bool TryColumn(string name, out IbtColumn column) => _byName.TryGetValue(name, out column!);

    /// <summary>
    /// SHA-256 over the decoded value matrix using the exact byte encoding the
    /// Python golden generator uses (tools/parity/emit_ibt_golden.py). Equal
    /// hashes prove this decoder read the same bytes as the Python oracle -
    /// byte-identity, not approximate agreement. The per-value packing
    /// re-narrows float32 with a single write, matching Python's struct.pack.
    /// </summary>
    public string MatrixSha256()
    {
        using var sha = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        byte[] scratch = new byte[12];
        byte[] element = new byte[8];
        foreach (IbtColumn column in Columns)
        {
            IbtVariable v = column.Variable;
            sha.AppendData(Encoding.UTF8.GetBytes(v.Name));
            BinaryPrimitives.WriteInt32LittleEndian(scratch.AsSpan(0, 4), v.TypeCode);
            BinaryPrimitives.WriteInt32LittleEndian(scratch.AsSpan(4, 4), v.Count);
            BinaryPrimitives.WriteInt32LittleEndian(scratch.AsSpan(8, 4), column.Length);
            sha.AppendData(scratch.AsSpan(0, 12));

            if (v.TypeCode == 0)
            {
                for (int i = 0; i < column.Length; i++)
                {
                    byte[] encoded = Encoding.UTF8.GetBytes(column.GetString(i));
                    BinaryPrimitives.WriteInt32LittleEndian(scratch.AsSpan(0, 4), encoded.Length);
                    sha.AppendData(scratch.AsSpan(0, 4));
                    sha.AppendData(encoded);
                }
                continue;
            }

            for (int i = 0; i < column.Length; i++)
            {
                object value = column.GetValue(i);
                if (value is object[] list)
                {
                    foreach (object item in list)
                    {
                        AppendElement(sha, v.TypeCode, item, element);
                    }
                }
                else
                {
                    AppendElement(sha, v.TypeCode, value, element);
                }
            }
        }

        return Convert.ToHexString(sha.GetHashAndReset()).ToLowerInvariant();
    }

    private static void AppendElement(IncrementalHash sha, int typeCode, object value, byte[] buffer)
    {
        switch (typeCode)
        {
            case 1:
                buffer[0] = (bool)value ? (byte)1 : (byte)0;
                sha.AppendData(buffer.AsSpan(0, 1));
                break;
            case 2:
                BinaryPrimitives.WriteInt32LittleEndian(buffer.AsSpan(0, 4), (int)value);
                sha.AppendData(buffer.AsSpan(0, 4));
                break;
            case 3:
                BinaryPrimitives.WriteUInt32LittleEndian(buffer.AsSpan(0, 4), (uint)value);
                sha.AppendData(buffer.AsSpan(0, 4));
                break;
            case 4:
                // Re-narrow the widened double to float32, matching struct.pack('<f', ...).
                BinaryPrimitives.WriteSingleLittleEndian(buffer.AsSpan(0, 4), (float)(double)value);
                sha.AppendData(buffer.AsSpan(0, 4));
                break;
            case 5:
                BinaryPrimitives.WriteDoubleLittleEndian(buffer.AsSpan(0, 8), (double)value);
                sha.AppendData(buffer.AsSpan(0, 8));
                break;
            default:
                throw new IbtTypeException($"unsupported SDK type code {typeCode}");
        }
    }
}

public class IbtException : Exception
{
    public IbtException(string message) : base(message) { }
}

public sealed class IbtFormatException : IbtException
{
    public IbtFormatException(string message) : base(message) { }
}

public sealed class IbtBoundsException : IbtException
{
    public IbtBoundsException(string message) : base(message) { }
}

public sealed class IbtTypeException : IbtException
{
    public IbtTypeException(string message) : base(message) { }
}
