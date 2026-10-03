/** Defensive readers for backend subtrees the contract passes through unchanged (`x-source`). Unknown fields are never an error. */
export type Obj = Record<string, unknown>;

export const obj = (v: unknown): Obj => (v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : {});
export const str = (v: unknown): string => (typeof v === "string" ? v : "");
export const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
export const bool = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);
export const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
export const strings = (v: unknown): string[] => arr(v).filter((x): x is string => typeof x === "string" && x.length > 0);
/** Dotted-path read: `at(run, "pace.early_to_late_delta_s")`. */
export const at = (v: unknown, path: string): unknown => path.split(".").reduce<unknown>((o, k) => (o && typeof o === "object" ? (o as Obj)[k] : undefined), v);
