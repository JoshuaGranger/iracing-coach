import { routeSynthetic, type FixtureResponse } from "../fixtures/api";
import { buildSyntheticAnalysis } from "../fixtures/synthetic";

/** Node-side entry bundled by perf/g2.mjs: the 82-lap synthetic analysis and its API router. */
export function createFixtureApi(): { analysisId: string; route(path: string): FixtureResponse; ccolBytes: number } {
  const analysis = buildSyntheticAnalysis({ laps: 82 });
  return {
    analysisId: analysis.analysisId,
    route: (path) => routeSynthetic(analysis, path),
    ccolBytes: analysis.ccol.byteLength,
  };
}
