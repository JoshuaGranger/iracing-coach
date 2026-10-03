import { routeSynthetic, type FixtureResponse } from "../fixtures/api";
import { routeStatic, type StaticAnalysisFiles } from "../fixtures/static-api";
import { buildSyntheticAnalysis } from "../fixtures/synthetic";

/** Node-side entry bundled by perf/harness-lib.mjs: an analysis's view endpoints, from the committed files or the synthetic fallback. */
export interface FixtureApi {
  analysisId: string;
  route(path: string): FixtureResponse;
  ccolBytes: number;
}

/** The 82-lap synthetic analysis and its API router (the `--fixture synthetic` fallback). */
export function createFixtureApi(): FixtureApi {
  const analysis = buildSyntheticAnalysis({ laps: 82 });
  return {
    analysisId: analysis.analysisId,
    route: (path) => routeSynthetic(analysis, path),
    ccolBytes: analysis.ccol.byteLength,
  };
}

/** The committed `test-data/g2/` view files served as they are. */
export function createStaticFixtureApi(files: StaticAnalysisFiles): FixtureApi {
  return {
    analysisId: files.analysisId,
    route: (path) => routeStatic(files, path),
    ccolBytes: files.ccol.byteLength,
  };
}
