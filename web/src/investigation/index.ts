/**
 * Web investigation (W8): Race, Runs, Sections and Traces on one viewport-bound workspace.
 * The app shell mounts `InvestigationWorkspace` with an `AnalysisLoader`; everything else is exported
 * for tests and the G2 harness.
 */
export { InvestigationWorkspace } from "./ui/InvestigationWorkspace";
export type { InvestigationSelection, InvestigationWorkspaceProps } from "./ui/InvestigationWorkspace";
export { createAnalysisLoader, HttpError, ProjectionPendingError } from "./load";
export type { AnalysisHandle, AnalysisLoader, LoaderOptions, Overview } from "./load";
export { createAnalysisCache, CACHE_MAX_BYTES, CACHE_MAX_ENTRIES } from "./cache";
export type { AnalysisCache } from "./cache";
export { decodeCcol, encodeCcol, CcolError } from "./ccol";
export type { TraceTable } from "./ccol";
export { parseInvestigation, InvestigationFormatError } from "./investigation-model";
export type { Investigation } from "./investigation-model";
export { INVESTIGATION_VIEWS } from "./types";
export type { InvestigationView, LastInvestigation, TraceLayoutBody } from "./types";
export { criticalLayoutBody, defaultLayoutBody } from "./layout-model";
