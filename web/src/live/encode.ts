import { CHANNELS, CHANNEL_BYTES, TABLE_VERSION } from "../gen/live-channels.ts";
import type { LiveCue } from "./frame.ts";

// Not imported from frame.ts: the mock server loads this file with Node type stripping, which cannot run frame.ts parameter properties.
const FRAME_HEADER_LEN = 38;

/**
 * Frame encoder for tests and the mock server (the collector's encoder is the Rust one in coach-live).
 * Writes every channel of this table; `null` values are absent in the presence bits.
 */
export interface EncodeInput {
  flags?: number;
  seq?: number;
  sessionEpoch?: number;
  capturedUnixMs?: number;
  sessionTime?: number;
  tick?: number;
  tickRate?: number;
  tableVersion?: number;
  /** Per channel index; omitted or null = absent. */
  values: readonly (number | null | undefined)[];
  cue?: LiveCue | null;
  /** Declare more channels than this table has (a newer sender); extras are absent. */
  extraChannels?: number;
}

export function encodeFrame(input: EncodeInput): Uint8Array {
  const n = CHANNELS.length + (input.extraChannels ?? 0);
  const presenceLen = Math.ceil(n / 8);
  let size = FRAME_HEADER_LEN + presenceLen;
  for (let i = 0; i < CHANNELS.length; i++) if (input.values[i] != null) size += CHANNEL_BYTES[CHANNELS[i]!.type];
  const cue = input.cue ?? null;
  const writeCue = !input.extraChannels;
  if (writeCue) size += 4 + 4 * (cue?.args.length ?? 0);
  const buf = new Uint8Array(size);
  const view = new DataView(buf.buffer);
  buf[0] = 0x4c;
  buf[1] = 0x46;
  buf[2] = 1;
  buf[3] = input.flags ?? 1;
  view.setUint32(4, input.seq ?? 0, true);
  view.setUint32(8, input.sessionEpoch ?? 1, true);
  view.setBigUint64(12, BigInt(Math.trunc(input.capturedUnixMs ?? 0)), true);
  view.setFloat64(20, input.sessionTime ?? 0, true);
  view.setUint32(28, input.tick ?? 0, true);
  view.setUint16(32, input.tickRate ?? 60, true);
  view.setUint16(34, input.tableVersion ?? (input.extraChannels ? TABLE_VERSION + 1 : TABLE_VERSION), true);
  view.setUint16(36, n, true);
  let at = FRAME_HEADER_LEN + presenceLen;
  for (let i = 0; i < CHANNELS.length; i++) {
    const v = input.values[i];
    if (v == null) continue;
    buf[FRAME_HEADER_LEN + (i >> 3)]! |= 1 << (i & 7);
    switch (CHANNELS[i]!.type) {
      case "f32":
        view.setFloat32(at, v, true);
        break;
      case "f64":
        view.setFloat64(at, v, true);
        break;
      case "u16":
        view.setUint16(at, v, true);
        break;
      case "u32":
        view.setUint32(at, v, true);
        break;
      case "i8":
        view.setInt8(at, v);
        break;
      default:
        buf[at] = v;
    }
    at += CHANNEL_BYTES[CHANNELS[i]!.type];
  }
  if (writeCue) {
    buf[at] = cue?.id ?? 0;
    buf[at + 1] = cue?.priority ?? 0;
    buf[at + 2] = cue?.evidence ?? 0;
    buf[at + 3] = cue?.args.length ?? 0;
    cue?.args.forEach((a, j) => view.setFloat32(at + 4 + 4 * j, a, true));
  }
  return buf;
}
