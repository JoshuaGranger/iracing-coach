import { theme } from "../../gen/theme";
import { boundedCursorLaps } from "../geometry/budget";
import type { MapPoint } from "../geometry/map-model";
import {
  aggregateSeries,
  hasSignalData,
  lapColor,
  plotColumn,
  referenceLapFor,
  resolve,
  signalRange,
  smoothedValue,
  type AggregateSeries,
  type Range,
  type TraceData,
} from "../geometry/trace-model";
import type { TraceRowSpec } from "../types";
import { tooltipCapacityFor } from "./cursor-core";

/**
 * Chart layout and cursor configuration. The chart is drawn in CSS pixels (viewBox == element size), so
 * the cursor needs no coordinate scaling: markers and tooltips are placed with the same numbers the
 * paths were built from.
 */

/** The root font size the geometry below was written for; `unit` arguments are pixels per rem (16 by default, more at the Larger and Largest text sizes). */
const DEFAULT_UNIT = 16;

/**
 * Geometry of the value cards the cursor draws on the canvas, in CSS pixels. The card text is the `--text-data` size of the theme
 * (config/theme.dark.json) at the current root font size, so it reads like the rest of the app and follows the text-size step.
 */
export interface CardMetrics {
  fontPx: number;
  /** Height of one lap line in a card. */
  slotHeight: number;
  padX: number;
  padY: number;
  gap: number;
  swatch: number;
}

export function cardMetrics(unit: number = DEFAULT_UNIT): CardMetrics {
  const scale = unit / DEFAULT_UNIT;
  const fontPx = theme.typography.dataPx * scale;
  return { fontPx, slotHeight: Math.ceil(fontPx * 1.45), padX: Math.round(6 * scale), padY: Math.round(3 * scale), gap: Math.round(4 * scale), swatch: Math.round(10 * scale) };
}

export interface ChartMetrics {
  /** Root font size the metrics were built for, in px per rem. */
  unit: number;
  width: number;
  height: number;
  plotLeft: number;
  plotWidth: number;
  chartTop: number;
  chartBottom: number;
  rowHeight: number;
  plotHeight: number;
  rowCount: number;
}

/** Row frames for `rowCount` charts in a `width x height` pixel area, with a band below for the x ticks. A row holds a two-line label and a value card, so its floor follows the root font size. */
export function chartMetrics(width: number, height: number, rowCount: number, unit: number = DEFAULT_UNIT): ChartMetrics {
  const scale = unit / DEFAULT_UNIT;
  const plotLeft = 6;
  const plotRight = 10;
  const chartTop = 6;
  const axisBand = Math.round(20 * scale);
  const rows = Math.max(1, rowCount);
  const rowHeight = Math.max(Math.round(44 * scale), (height - chartTop - axisBand) / rows);
  const plotHeight = Math.max(Math.round(36 * scale), rowHeight - 8);
  return {
    unit,
    width,
    height,
    plotLeft,
    plotWidth: Math.max(40, width - plotLeft - plotRight),
    chartTop,
    chartBottom: chartTop + rows * rowHeight - (rowHeight - plotHeight),
    rowHeight,
    plotHeight,
    rowCount: rows,
  };
}

export interface CursorSignal {
  id: string;
  shortLabel: string;
  unit: string;
  digits: number | null;
  min: number;
  max: number;
}

export interface CursorRow {
  rowId: string;
  top: number;
  plotHeight: number;
  signals: CursorSignal[];
  /** Widest tooltip text over the detailed laps' bins, in characters. */
  widestText: number;
}

export interface CursorTrace {
  lap: number;
  color: string;
  /** `rowValues[row][signal]` is a bin-length array of smoothed display values (NaN missing). */
  rowValues: Float64Array[][];
}

export interface CursorConfig {
  key: string;
  bins: number;
  x: Float64Array;
  metrics: ChartMetrics;
  card: CardMetrics;
  rows: CursorRow[];
  traces: CursorTrace[];
  logicalTraceCount: number;
  tooltipCapacity: number;
  aggregate: AggregateSeries;
  trackPoints: MapPoint[];
  distanceStrip: boolean;
  initialFraction: number;
}

export interface RowDisplay {
  row: TraceRowSpec;
  top: number;
  signals: { id: string; index: number; range: Range }[];
}

export interface CursorInputs {
  data: TraceData;
  rows: readonly TraceRowSpec[];
  selectedLaps: readonly number[];
  spotlightLap: number | null;
  metrics: ChartMetrics;
  trackPoints: MapPoint[];
  initialFraction: number;
  key: string;
}

export function buildCursorConfig(input: CursorInputs): CursorConfig {
  const { data, metrics } = input;
  const card = cardMetrics(metrics.unit);
  const bins = data.bins;
  const ordered = input.selectedLaps
    .slice()
    .sort((a, b) => (input.spotlightLap === a ? 0 : 1) - (input.spotlightLap === b ? 0 : 1) || a - b);
  const reference = referenceLapFor(data, ordered);
  const lapIndices = ordered.map((lap) => data.lapIndex.get(lap)).filter((index): index is number => index !== undefined);
  const detailLaps = boundedCursorLaps(ordered, input.spotlightLap, reference, data.budgets.cursor_detail_traces);
  const rows: CursorRow[] = input.rows.map((row, rowIndex) => {
    const signals: CursorSignal[] = [];
    for (const id of row.signals) {
      if (!hasSignalData(data, id, lapIndices, reference)) continue;
      const resolved = resolve(data, id);
      if (!resolved) continue;
      const range = signalRange(data, id, lapIndices, reference);
      signals.push({ id, shortLabel: resolved.shortLabel, unit: resolved.unit, digits: resolved.digits, min: range.min, max: range.max });
    }
    return { rowId: row.row_id, top: metrics.chartTop + rowIndex * metrics.rowHeight, plotHeight: metrics.plotHeight, signals, widestText: 8 };
  });
  const traces: CursorTrace[] = detailLaps.map((lap) => {
    const lapIndex = data.lapIndex.get(lap)!;
    const rowValues = rows.map((row) =>
      row.signals.map((signal) => {
        const column = plotColumn(data, signal.id, reference);
        const resolved = resolve(data, signal.id);
        const out = new Float64Array(bins).fill(Number.NaN);
        if (column && resolved) for (let bin = 0; bin < bins; bin++) out[bin] = smoothedValue(column, lapIndex, bin, bins, signal.id, resolved.step);
        return out;
      }),
    );
    return { lap, color: lapColor(data, lap), rowValues };
  });
  // Tooltip width follows the longest digits a row can show; sampled so config build stays cheap.
  rows.forEach((row, rowIndex) => {
    const perSignal = row.signals.map((signal) => (signal.id === "steering" || signal.id === "steering-wheel-angle" ? 13 : signal.id === "delta" ? 10 : 11) + (row.signals.length > 1 ? signal.shortLabel.length + 1 : 0));
    const hasData = traces.some((trace) => trace.rowValues[rowIndex]!.some((values) => values.some((value) => Number.isFinite(value))));
    row.widestText = hasData ? Math.max(8, perSignal.reduce((sum, n) => sum + n, 0) + (perSignal.length > 1 ? 3 : 0)) : 8;
  });
  return {
    key: input.key,
    bins,
    x: data.x,
    metrics,
    card,
    rows,
    traces,
    logicalTraceCount: ordered.length,
    tooltipCapacity: tooltipCapacityFor(metrics.rowHeight, card.slotHeight),
    aggregate: aggregateSeries(data, lapIndices, reference),
    trackPoints: input.trackPoints,
    distanceStrip: input.trackPoints.length === 0,
    initialFraction: input.initialFraction,
  };
}
