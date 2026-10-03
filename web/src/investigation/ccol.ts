import type { TraceBlobHeader } from "./types";

/**
 * CCOL decoder (`GET /api/v1/analyses/{id}/traces`, `application/vnd.coach.columns`).
 *
 * Layout: bytes 0-3 "CCOL", u16 version 1, u16 reserved 0, u32 header length H (little endian),
 * H bytes of UTF-8 JSON (`TraceBlobHeader`), zero padding to an 8-byte boundary of the file, then each
 * channel's little-endian typed array of `laps.length x bins` values (lap-major) at `offset` from the
 * start of that data section. Integer channels carry a `missing` sentinel; float channels use NaN.
 */

export class CcolError extends Error {
  constructor(message: string) {
    super(`CCOL: ${message}`);
    this.name = "CcolError";
  }
}

const MAGIC = [0x43, 0x43, 0x4f, 0x4c];
const FIXED_HEADER_BYTES = 12;
const ELEMENT_BYTES = { f64: 8, i16: 2, u8: 1 } as const;

export type CcolArray = Float64Array | Int16Array | Uint8Array;

export interface CcolChannel {
  id: string;
  dtype: "f64" | "i16" | "u8";
  /** Sentinel for integer dtypes; null for floats (NaN marks a missing float). */
  missing: number | null;
  /** Zero-copy view over the response buffer, `laps x bins`, lap-major. */
  raw: CcolArray;
}

export interface TraceTable {
  analysisId: string;
  projectionVersion: number;
  bins: number;
  laps: number[];
  channels: Map<string, CcolChannel>;
  /** Size of the underlying response in bytes (cache accounting). */
  byteLength: number;
}

function align8(value: number): number {
  return (value + 7) & ~7;
}

export function decodeCcol(buffer: ArrayBuffer): TraceTable {
  if (buffer.byteLength < FIXED_HEADER_BYTES) throw new CcolError("truncated container");
  const view = new DataView(buffer);
  for (let index = 0; index < MAGIC.length; index++) {
    if (view.getUint8(index) !== MAGIC[index]) throw new CcolError("bad magic");
  }
  const version = view.getUint16(4, true);
  if (version !== 1) throw new CcolError(`unsupported version ${version}`);
  const headerLength = view.getUint32(8, true);
  if (FIXED_HEADER_BYTES + headerLength > buffer.byteLength) throw new CcolError("header exceeds the container");

  let header: TraceBlobHeader;
  try {
    header = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(new Uint8Array(buffer, FIXED_HEADER_BYTES, headerLength))) as TraceBlobHeader;
  } catch (error) {
    throw new CcolError(`header is not valid JSON (${error instanceof Error ? error.message : String(error)})`);
  }
  if (header.format !== "CCOL" || header.version !== 1) throw new CcolError("header format or version mismatch");
  if (!Number.isInteger(header.bins) || header.bins <= 0) throw new CcolError("bins must be a positive integer");
  if (!Array.isArray(header.laps) || !Array.isArray(header.channels)) throw new CcolError("header lacks laps or channels");

  const dataStart = align8(FIXED_HEADER_BYTES + headerLength);
  const expectedValues = header.laps.length * header.bins;
  const channels = new Map<string, CcolChannel>();
  for (const entry of header.channels) {
    const width = ELEMENT_BYTES[entry.dtype];
    if (width === undefined) throw new CcolError(`channel ${entry.id}: unknown dtype ${String(entry.dtype)}`);
    if (entry.byte_length !== expectedValues * width) {
      throw new CcolError(`channel ${entry.id}: byte_length ${entry.byte_length} does not match ${expectedValues} values`);
    }
    const start = dataStart + entry.offset;
    if (entry.offset % 8 !== 0) throw new CcolError(`channel ${entry.id}: offset is not 8-byte aligned`);
    if (start + entry.byte_length > buffer.byteLength) throw new CcolError(`channel ${entry.id}: data exceeds the container`);
    if (channels.has(entry.id)) throw new CcolError(`channel ${entry.id} is listed twice`);
    const raw: CcolArray =
      entry.dtype === "f64" ? new Float64Array(buffer, start, expectedValues)
      : entry.dtype === "i16" ? new Int16Array(buffer, start, expectedValues)
      : new Uint8Array(buffer, start, expectedValues);
    channels.set(entry.id, {
      id: entry.id,
      dtype: entry.dtype,
      missing: entry.dtype === "f64" ? null : (entry.missing ?? null),
      raw,
    });
  }
  return {
    analysisId: header.analysis_id,
    projectionVersion: header.projection_version,
    bins: header.bins,
    laps: header.laps.slice(),
    channels,
    byteLength: buffer.byteLength,
  };
}

/**
 * Joins CCOL bodies of consecutive lap ranges of one analysis into one table (the 413 remedy: the browser
 * asks for halves and joins them). Every part must carry the same bins and channels; a channel whose
 * integer width or sentinel differs between parts (the encoder picks it per body) is joined as f64 with
 * NaN for missing.
 */
export function joinCcol(parts: readonly TraceTable[]): TraceTable {
  if (parts.length === 0) throw new CcolError("nothing to join");
  const first = parts[0]!;
  if (parts.length === 1) return first;
  for (const part of parts) {
    if (part.bins !== first.bins || part.analysisId !== first.analysisId) throw new CcolError("parts of one analysis must share bins");
    if (part.channels.size !== first.channels.size) throw new CcolError("parts carry different channels");
    for (const id of first.channels.keys()) if (!part.channels.has(id)) throw new CcolError(`channel ${id} is missing from a part`);
  }
  const total = parts.reduce((sum, part) => sum + part.laps.length * part.bins, 0);
  const channels = new Map<string, CcolChannel>();
  for (const [id, head] of first.channels) {
    const members = parts.map((part) => part.channels.get(id)!);
    const uniform = members.every((member) => member.dtype === head.dtype && member.missing === head.missing);
    const dtype = uniform ? head.dtype : "f64";
    const raw: CcolArray = dtype === "f64" ? new Float64Array(total) : dtype === "i16" ? new Int16Array(total) : new Uint8Array(total);
    let at = 0;
    for (const member of members) {
      const source = uniform ? member.raw : channelAsFloat(member);
      (raw as Float64Array).set(source as Float64Array, at);
      at += source.length;
    }
    channels.set(id, { id, dtype, missing: dtype === "f64" ? null : head.missing, raw });
  }
  return {
    analysisId: first.analysisId,
    projectionVersion: first.projectionVersion,
    bins: first.bins,
    laps: parts.flatMap((part) => part.laps),
    channels,
    byteLength: parts.reduce((sum, part) => sum + part.byteLength, 0),
  };
}

/** One value of a channel; a missing integer or NaN float returns NaN. */
export function channelValue(channel: CcolChannel, lapIndex: number, bin: number, bins: number): number {
  const raw = channel.raw[lapIndex * bins + bin];
  if (raw === undefined) return Number.NaN;
  if (channel.missing !== null && raw === channel.missing) return Number.NaN;
  return raw;
}

/** Whole channel as Float64 with NaN for missing; integer channels are converted once, floats are returned as is. */
export function channelAsFloat(channel: CcolChannel): Float64Array {
  if (channel.raw instanceof Float64Array) return channel.raw;
  const out = new Float64Array(channel.raw.length);
  const missing = channel.missing;
  for (let index = 0; index < out.length; index++) {
    const value = channel.raw[index] as number;
    out[index] = missing !== null && value === missing ? Number.NaN : value;
  }
  return out;
}

export interface CcolEncodeChannel {
  id: string;
  dtype: "f64" | "i16" | "u8";
  /** NaN marks a missing value; integer channels write `missing` in its place. */
  values: ArrayLike<number>;
  missing?: number | null;
}

/** Encoder used by fixtures and tests (the server's projector writes the real container). */
export function encodeCcol(
  analysisId: string,
  projectionVersion: number,
  bins: number,
  laps: number[],
  channels: CcolEncodeChannel[],
): ArrayBuffer {
  const expected = laps.length * bins;
  let offset = 0;
  const entries = channels.map((channel) => {
    if (channel.values.length !== expected) throw new CcolError(`channel ${channel.id}: ${channel.values.length} values, expected ${expected}`);
    const byteLength = expected * ELEMENT_BYTES[channel.dtype];
    const entry = { id: channel.id, dtype: channel.dtype, offset, byte_length: byteLength, missing: channel.dtype === "f64" ? null : (channel.missing ?? null) };
    offset = align8(offset + byteLength);
    return entry;
  });
  const header: TraceBlobHeader = {
    format: "CCOL",
    version: 1,
    analysis_id: analysisId,
    projection_version: projectionVersion,
    bins,
    laps,
    channels: entries,
  };
  const headerBytes = new TextEncoder().encode(JSON.stringify(header));
  const dataStart = align8(FIXED_HEADER_BYTES + headerBytes.length);
  const buffer = new ArrayBuffer(dataStart + offset);
  const view = new DataView(buffer);
  MAGIC.forEach((byte, index) => view.setUint8(index, byte));
  view.setUint16(4, 1, true);
  view.setUint16(6, 0, true);
  view.setUint32(8, headerBytes.length, true);
  new Uint8Array(buffer, FIXED_HEADER_BYTES, headerBytes.length).set(headerBytes);
  channels.forEach((channel, index) => {
    const entry = entries[index]!;
    const start = dataStart + entry.offset;
    if (channel.dtype === "f64") {
      const target = new Float64Array(buffer, start, expected);
      for (let i = 0; i < expected; i++) target[i] = channel.values[i]!;
    } else if (channel.dtype === "i16") {
      const target = new Int16Array(buffer, start, expected);
      const sentinel = channel.missing ?? -32768;
      for (let i = 0; i < expected; i++) target[i] = Number.isNaN(channel.values[i]!) ? sentinel : channel.values[i]!;
    } else {
      const target = new Uint8Array(buffer, start, expected);
      const sentinel = channel.missing ?? 255;
      for (let i = 0; i < expected; i++) target[i] = Number.isNaN(channel.values[i]!) ? sentinel : channel.values[i]!;
    }
  });
  return buffer;
}
