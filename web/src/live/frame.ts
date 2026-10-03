import { CHANNELS, CHANNEL_BYTES, CUES, type ChannelSpec, type CueSpec, Evidence, Priority, TABLE_VERSION } from "../gen/live-channels.ts";

/** Live frame decoder (docs/08-rewrite/contracts/live-frame.md, version 1), driven by the generated channel table. */
export const FRAME_HEADER_LEN = 38;

export const FrameFlag = {
  SimConnected: 1,
  OnTrack: 2,
  InGarage: 4,
  OnPitRoad: 8,
  FirstFrame: 16,
  CoachingPaused: 32,
  Backlog: 64,
} as const;

export interface LiveCue {
  id: number;
  priority: number;
  evidence: number;
  args: number[];
}

export interface LiveFrame {
  flags: number;
  seq: number;
  sessionEpoch: number;
  capturedUnixMs: number;
  sessionTime: number;
  tick: number;
  tickRate: number;
  tableVersion: number;
  channelCount: number;
  /** Value per channel index of this decoder's table; null = not measured (absent, or not finite). */
  values: (number | null)[];
  cue: LiveCue | null;
}

export type FrameErrorCode = "too_short" | "bad_magic" | "bad_version" | "channel_count" | "truncated" | "bad_cue";

export class FrameError extends Error {
  constructor(
    readonly code: FrameErrorCode,
    message: string,
  ) {
    super(message);
    this.name = "FrameError";
  }
}

const PRIORITIES = new Set<number>(Object.values(Priority));

export interface DecodeTable {
  channels: readonly ChannelSpec[];
  cues: readonly CueSpec[];
  tableVersion: number;
}

export const DEFAULT_TABLE: DecodeTable = { channels: CHANNELS, cues: CUES, tableVersion: TABLE_VERSION };

export function decodeFrame(input: ArrayBuffer | Uint8Array, table: DecodeTable = DEFAULT_TABLE): LiveFrame {
  const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (bytes.length < FRAME_HEADER_LEN) throw new FrameError("too_short", "Frame shorter than its header");
  if (bytes[0] !== 0x4c || bytes[1] !== 0x46) throw new FrameError("bad_magic", "Not a live frame");
  if (bytes[2] !== 1) throw new FrameError("bad_version", `Unsupported frame version ${bytes[2]}`);
  const flags = bytes[3]!;
  const frameTableVersion = view.getUint16(34, true);
  const n = view.getUint16(36, true);
  const own = table.channels.length;
  if (n > own && frameTableVersion <= table.tableVersion) throw new FrameError("channel_count", `Frame has ${n} channels, table has ${own}`);
  const presenceLen = Math.ceil(n / 8);
  if (bytes.length < FRAME_HEADER_LEN + presenceLen) throw new FrameError("too_short", "Frame shorter than its presence bits");

  const values: (number | null)[] = new Array(own).fill(null);
  let at = FRAME_HEADER_LEN + presenceLen;
  const known = Math.min(n, own);
  for (let i = 0; i < known; i++) {
    if (((bytes[FRAME_HEADER_LEN + (i >> 3)]! >> (i & 7)) & 1) === 0) continue;
    const spec = table.channels[i]!;
    const width = CHANNEL_BYTES[spec.type];
    if (at + width > bytes.length) throw new FrameError("truncated", `Frame ends inside channel ${spec.id}`);
    let v: number;
    switch (spec.type) {
      case "f32":
        v = view.getFloat32(at, true);
        break;
      case "f64":
        v = view.getFloat64(at, true);
        break;
      case "u16":
        v = view.getUint16(at, true);
        break;
      case "u32":
        v = view.getUint32(at, true);
        break;
      case "i8":
        v = view.getInt8(at);
        break;
      case "bool8":
        v = bytes[at] === 0 ? 0 : 1;
        break;
      default:
        v = bytes[at]!; // u8, enum8
    }
    values[i] = Number.isFinite(v) ? v : null;
    at += width;
  }

  let cue: LiveCue | null = null;
  if (n <= own) {
    if (at + 4 > bytes.length) throw new FrameError("truncated", "Frame has no cue block");
    const id = bytes[at]!;
    const priority = bytes[at + 1]!;
    const evidence = bytes[at + 2]!;
    const k = bytes[at + 3]!;
    if (k > 4) throw new FrameError("bad_cue", `Cue has ${k} arguments`);
    if (at + 4 + 4 * k > bytes.length) throw new FrameError("bad_cue", "Cue arguments are cut off");
    if (id !== 0) {
      if (!table.cues.some((c) => c.id === id)) throw new FrameError("bad_cue", `Unknown cue ${id}`);
      if (!PRIORITIES.has(priority)) throw new FrameError("bad_cue", `Unknown cue priority ${priority}`);
      if (evidence > Evidence.Unavailable) throw new FrameError("bad_cue", `Unknown cue evidence ${evidence}`);
      const args: number[] = [];
      for (let j = 0; j < k; j++) args.push(view.getFloat32(at + 4 + 4 * j, true));
      cue = { id, priority, evidence, args };
    }
  }

  return {
    flags,
    seq: view.getUint32(4, true),
    sessionEpoch: view.getUint32(8, true),
    capturedUnixMs: Number(view.getBigUint64(12, true)),
    sessionTime: view.getFloat64(20, true),
    tick: view.getUint32(28, true),
    tickRate: view.getUint16(32, true),
    tableVersion: frameTableVersion,
    channelCount: n,
    values,
    cue,
  };
}

export const hasFlag = (f: LiveFrame, flag: number): boolean => (f.flags & flag) !== 0;
