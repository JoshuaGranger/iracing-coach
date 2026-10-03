// The part of the server's API the app shell reads around an investigation (drivers, one session, prefs, trace layouts),
// answered from the g2 fixture so views.mjs can drive the real app at #/d/<driver>/s/<session>/<view>.
export const SHELL_DRIVER = 1507810;
export const SHELL_SESSION = 1;

const json = (value, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(value) });
const notFound = () => json({ error: "not_found", message: "not in the shell fixture" }, 404);

/** `fixture` is the view fixture (`analysisId`, `route`); anything the shell does not read is a 404. */
export function routeShell(fixture, pathname) {
  if (pathname === "/api/v1/drivers") {
    return json({ drivers: [{ customer_id: SHELL_DRIVER, name: "Fixture Driver", irating: 1500, session_count: 1, last_session_at: "2026-09-20T18:00:00Z", live: false }] });
  }
  if (pathname === `/api/v1/drivers/${SHELL_DRIVER}/prefs`) return json({ customer_id: SHELL_DRIVER, version: 1 });
  if (pathname === `/api/v1/drivers/${SHELL_DRIVER}/trace-layouts`) return json({ layouts: [] });
  if (pathname === `/api/v1/sessions/${SHELL_SESSION}`) {
    const session = {
      session_id: SHELL_SESSION, customer_id: SHELL_DRIVER, group_key: "fixture-0", event_key: "subsession:1", session_type: "Race",
      track: "Fixture Speedway", track_config: "Oval", car_path: "fixture car", car_name: "Fixture Car", series_name: "Fixture Series", season_name: "2026 Season 3",
      subsession_id: 1, sim_session_num: 0, start_time_utc: "2026-09-20T18:00:00Z", recording_count: 1, analysis_state: "ready", analysis_reason: null, analysis_id: fixture.analysisId, overview: {},
    };
    const analysis = { analysis_id: fixture.analysisId, created_at: "2026-09-20T19:00:00Z", profile_version: "post-race-foundations-v23", lap_count: 83, projection_version: 1, projection_state: "ready", current: true };
    return json({ session, recordings: [], analyses: [analysis], setup_ids: [], notes_count: 0, event_sessions: [{ session_id: SHELL_SESSION, session_type: "Race" }] });
  }
  return notFound();
}
