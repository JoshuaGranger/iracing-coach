import { signalDefinition, SIGNAL_DEFINITIONS } from "./signals";
import type { TraceLayoutBody, TraceRowSpec } from "./types";

/**
 * Trace layout rules (AnalysisTraceLayouts.cs). Every operation returns a new body, or null when the
 * C# method returned false. Persistence is the app shell's job (`trace-layouts` endpoints).
 */

export const MAXIMUM_ROWS = 10;
export const MAXIMUM_SIGNALS_PER_ROW = 2;

export type SignalSlot = "primary" | "secondary";

let rowCounter = 0;
export function newRowId(): string {
  rowCounter += 1;
  const random = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID().replace(/-/g, "") : `${Date.now().toString(36)}${rowCounter}`;
  return `trace-row-${random}`;
}

function row(primary: string, secondary = "", id = newRowId()): TraceRowSpec {
  return { row_id: id, signals: secondary ? [primary, secondary] : [primary] };
}

/** The factory layout (DefaultRows). Ids are stable so a saved reference to the default survives. */
export function defaultLayoutBody(): TraceLayoutBody {
  const rows: [string, string?][] = [
    ["speed"], ["delta"], ["throttle"], ["brake"], ["tire-wear"], ["gear"], ["rpm"], ["steering"], ["slip", "yaw"], ["lateral-g", "longitudinal-g"],
  ];
  return { rows: rows.map(([primary, secondary], index) => row(primary, secondary ?? "", `default-row-${index + 1}`)) };
}

/** The six critical channels visible together at 1280x720 (design rule 3); the default for new users. */
export function criticalLayoutBody(): TraceLayoutBody {
  return {
    rows: [row("speed", "", "critical-speed"), row("delta", "", "critical-delta"), row("throttle", "", "critical-throttle"), row("brake", "", "critical-brake"), row("steering", "", "critical-steering"), row("slip", "", "critical-slip")],
  };
}

function steeringPair(first: string, second: string): boolean {
  return (first === "steering" && second === "steering-wheel-angle") || (first === "steering-wheel-angle" && second === "steering");
}

function validSecond(primary: string, secondary: string | undefined): boolean {
  if (!secondary) return true;
  return signalDefinition(primary) !== undefined && signalDefinition(secondary) !== undefined && primary !== secondary && !steeringPair(primary, secondary);
}

function clone(body: TraceLayoutBody): TraceLayoutBody {
  return { rows: body.rows.map((entry) => ({ ...entry, signals: entry.signals.slice() })) };
}

export function validateAndRepair(body: TraceLayoutBody): { body: TraceLayoutBody; changed: boolean } {
  if (body.rows.length === 0) return { body: defaultLayoutBody(), changed: true };
  const ids = new Set<string>();
  let changed = body.rows.length > MAXIMUM_ROWS;
  const rows = body.rows.slice(0, MAXIMUM_ROWS).map((source) => {
    const primary = source.signals[0] !== undefined && signalDefinition(source.signals[0]) ? source.signals[0] : "speed";
    const secondaryRaw = source.signals[1];
    const secondary = secondaryRaw !== undefined && validSecond(primary, secondaryRaw) ? secondaryRaw : "";
    const id = !source.row_id || ids.has(source.row_id) ? newRowId() : source.row_id;
    ids.add(id);
    if (primary !== source.signals[0] || secondary !== (secondaryRaw ?? "") || id !== source.row_id || source.signals.length > MAXIMUM_SIGNALS_PER_ROW) changed = true;
    return { ...source, row_id: id, signals: secondary ? [primary, secondary] : [primary] };
  });
  return { body: { rows }, changed };
}

function indexOfRow(body: TraceLayoutBody, rowId: string): number {
  return body.rows.findIndex((entry) => entry.row_id === rowId);
}

export function addRow(body: TraceLayoutBody): TraceLayoutBody | null {
  const repaired = validateAndRepair(body).body;
  if (repaired.rows.length >= MAXIMUM_ROWS) return null;
  const used = new Set(repaired.rows.flatMap((entry) => entry.signals));
  const signal = SIGNAL_DEFINITIONS.find((candidate) => !used.has(candidate.id)) ?? SIGNAL_DEFINITIONS[0]!;
  return insertSignalRow(repaired, signal.id, repaired.rows.length);
}

export function insertSignalRow(body: TraceLayoutBody, signalId: string, targetIndex: number): TraceLayoutBody | null {
  const repaired = validateAndRepair(body).body;
  const id = signalId.trim();
  if (repaired.rows.length >= MAXIMUM_ROWS || !signalDefinition(id) || targetIndex < 0 || targetIndex > repaired.rows.length) return null;
  const next = clone(repaired);
  next.rows.splice(targetIndex, 0, row(id));
  return next;
}

export function removeRow(body: TraceLayoutBody, rowId: string): TraceLayoutBody | null {
  const repaired = validateAndRepair(body).body;
  if (repaired.rows.length <= 1) return null;
  const index = indexOfRow(repaired, rowId);
  if (index < 0) return null;
  const next = clone(repaired);
  next.rows.splice(index, 1);
  return next;
}

export function moveRowToIndex(body: TraceLayoutBody, rowId: string, targetIndex: number): TraceLayoutBody | null {
  const repaired = validateAndRepair(body).body;
  const source = indexOfRow(repaired, rowId);
  if (source < 0 || targetIndex < 0 || targetIndex >= repaired.rows.length || source === targetIndex) return null;
  const next = clone(repaired);
  const [moved] = next.rows.splice(source, 1);
  next.rows.splice(targetIndex, 0, moved!);
  return next;
}

export function moveRow(body: TraceLayoutBody, rowId: string, direction: number): TraceLayoutBody | null {
  const index = indexOfRow(validateAndRepair(body).body, rowId);
  return index < 0 ? null : moveRowToIndex(body, rowId, index + Math.sign(direction));
}

/** Drop a signal onto a chart: it becomes (or replaces) the dashed second trace. */
export function placeSignal(body: TraceLayoutBody, rowId: string, signalId: string): TraceLayoutBody | null {
  const repaired = validateAndRepair(body).body;
  const id = signalId.trim();
  const index = indexOfRow(repaired, rowId);
  if (index < 0 || !signalDefinition(id)) return null;
  const target = repaired.rows[index]!;
  if (target.signals.includes(id)) return null;
  if (steeringPair(target.signals[0]!, id)) return null;
  const next = clone(repaired);
  next.rows[index]!.signals = [target.signals[0]!, id];
  return next;
}

export function removeSignal(body: TraceLayoutBody, rowId: string, signalId: string): TraceLayoutBody | null {
  const repaired = validateAndRepair(body).body;
  const index = indexOfRow(repaired, rowId);
  if (index < 0) return null;
  const target = repaired.rows[index]!;
  const slot = target.signals.indexOf(signalId.trim());
  if (slot < 0) return null;
  const next = clone(repaired);
  if (slot === 1) {
    next.rows[index]!.signals = [target.signals[0]!];
    return next;
  }
  if (target.signals.length > 1) {
    next.rows[index]!.signals = [target.signals[1]!];
    return next;
  }
  if (repaired.rows.length <= 1) return null;
  next.rows.splice(index, 1);
  return next;
}

export function secondarySignalOptions(primary: string): string[] {
  if (!signalDefinition(primary)) return [];
  return SIGNAL_DEFINITIONS.filter((signal) => signal.id !== primary && !steeringPair(primary, signal.id)).map((signal) => signal.id);
}

/** The bounded set a drop may act on, for the drag editor's preview text. */
export type LayoutAction =
  | { type: "move-row"; rowId: string; targetIndex: number }
  | { type: "insert-signal"; signalId: string; targetIndex: number }
  | { type: "place-signal"; rowId: string; signalId: string };

export function applyLayoutAction(body: TraceLayoutBody, action: LayoutAction): TraceLayoutBody | null {
  switch (action.type) {
    case "move-row": return moveRowToIndex(body, action.rowId, action.targetIndex);
    case "insert-signal": return insertSignalRow(body, action.signalId, action.targetIndex);
    case "place-signal": return placeSignal(body, action.rowId, action.signalId);
  }
}
