import type { components } from "../gen/api";

type Schemas = components["schemas"];

export type RaceViewData = Schemas["RaceView"];
export type RunsViewData = Schemas["RunsView"];
export type InvestigationViewData = Schemas["InvestigationView"];
export type TraceCatalogData = Schemas["TraceCatalog"];
export type TraceSignalData = Schemas["TraceSignal"];
export type TraceLapData = Schemas["TraceLap"];
export type TrackGeometryData = Schemas["TrackGeometry"];
export type PathSeriesData = Schemas["PathSeries"];
export type SteeringBasisData = Schemas["SteeringBasis"];
export type LapData = Schemas["Lap"];
export type TraceBlobHeader = Schemas["TraceBlobHeader"];
export type LastInvestigation = Schemas["LastInvestigation"];

export type InvestigationView = "race" | "runs" | "sections" | "traces" | "results";
export const INVESTIGATION_VIEWS: readonly InvestigationView[] = ["race", "runs", "sections", "traces", "results"];

/** Budgets the catalog states (AnalysisRenderBudget); the constants below are the contract's `const` values. */
export interface RenderBudgets {
  vertex_budget_per_signal: number;
  min_points_per_trace: number;
  max_color_groups: number;
  cursor_detail_traces: number;
  max_trace_rows: number;
}

export const DEFAULT_BUDGETS: RenderBudgets = {
  vertex_budget_per_signal: 48_000,
  min_points_per_trace: 48,
  max_color_groups: 20,
  cursor_detail_traces: 24,
  max_trace_rows: 10,
};

export interface Point2 {
  x: number;
  y: number;
}

/** A row of a trace layout: up to two signals per chart (TraceLayoutWrite.body.rows). */
export interface TraceRowSpec {
  row_id: string;
  label?: string | null;
  signals: string[];
}

export interface TraceLayoutBody {
  rows: TraceRowSpec[];
}

export type Json = { [key: string]: unknown };

export function asRecord(value: unknown): Json {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Json) : {};
}

export function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function str(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}
