# Garage61, authentication, and seasonal web research

## Authentication

Use the official Garage61 API with a Bearer personal access token for this one-user plugin. Store it only through `scripts/configure-garage61.ps1`, which uses Windows user-bound encryption. Check `/api/v1/me` before a sync and report granted API permissions.

Pin authenticated production requests to the exact HTTPS `garage61.net` origin. Reject alternate hosts and reject cross-origin redirects before forwarding `Authorization`; HTTP(S) loopback origins are test-only. Do not weaken this rule to accommodate an unofficial proxy.

Do not reuse or inspect Garage61 Agent internal tokens, `state.db`, browser cookies, profile databases, or passwords. Use a persistent signed-in browser only for interactive capabilities that the official API does not expose.

Garage61 developer sources:

- `https://garage61.net/developer/authentication`
- `https://garage61.net/developer/endpoints`
- `https://garage61.net/api/openapi/v1.json`
- `https://garage61.net/developer/changelog`

## Fast-path boundary

The default post-race and race-planning response is local-first and `cache_only`. Read a valid seasonal Garage61 index and cached CSVs when present, but do not browse, authenticate, sync, or refresh before rendering the Race Card. Network enrichment is optional follow-up work unless the user explicitly asks for current Garage61 or web research.

A missing, stale, unauthorized, or still-pending Garage61 component does not block local coaching. Use relative local guidance and render exact targets as `[U] Exact target unavailable` until a cached or newly authorized comparison has `comparison_quality.status: usable`. Do not retry the same authentication or sync request throughout a race-analysis turn, and do not poll a pending API application on every race.

Canonicalize each Garage61/web request by season, car, exact layout, setup scope, conditions, filters, and requested artifact. Reuse the first result within the turn; never issue an identical query merely because a later report section needs the same evidence.

## API comparison limits

The normal `driving_data` permission is limited to personal and teammate data. Only specially approved applications can search all laps otherwise visible to the authenticated user. Always record:

- authentication method and health;
- granted permissions;
- query filters;
- result count;
- whether the pool is `own/team`, `approved global-visible`, `website-selected`, or `manual export`.

Never label own/team results as the entire Garage61 field. Keep `global_visible_laps_approved` false until Garage61 has explicitly granted the special capability; set it to `true` only as a deliberate local opt-in after approval. A healthy token, `driving_data` permission, or signed-in web session does not imply global-visible approval.

## Candidate ranking

Map iRacing car/track IDs to Garage61 platform IDs. Query exact track and fixed/open cohorts separately. Preserve dynamic response and CSV fields.

Rank with a transparent distance score across:

1. season;
2. setup type;
3. tire compound and BoP;
4. track usage/wetness and track/air temperature;
5. fuel level/used;
6. clean/complete telemetry;
7. driver rating and pace gap.

Favor 3–5 drivers modestly faster than Joshua and 1–2 elite examples. Avoid overfitting coaching to one unusual setup or driving style.

The public API exposes telemetry CSV downloads for authorized laps but does not expose arbitrary lap setup contents. Use local IBT setup metadata for Joshua. For another driver's open setup, use an authorized data-pack setup, a manually synced/exported setup, or the visible website; otherwise state that the comparison conflates setup and driving.

## Telemetry alignment and targets

Inspect `manifest.files.garage61` before querying. Reuse a valid seasonal Garage61 index and cached CSVs; sync only when that component is missing/stale, credentials newly become available, or the user explicitly requests a refresh. Do not treat a fresh car/track research state as proof that the optional Garage61 component exists, and do not retry a still-pending API request every race.

Download authorized reference CSVs into the seasonal bundle. Preserve original headers and unknown columns, while mapping recognized lap distance, speed, brake, throttle, steering, and lateral-acceleration fields. Normalize percent/fraction and declared or inferred speed/steering units; record every assumption.

Align each reference with Joshua's local track profile in the same lap-distance bins. Require sufficient shared coverage and speed before treating a reference as usable. Build the benchmark from same-setup usable laps when possible; if none exist, allow a clearly labeled `cross_setup_fallback`.

Archive:

- per-lap `reference_comparisons`, including coverage, aligned bins, and quality status;
- the representative median `benchmark_profile`;
- `coaching_targets` for telemetry-derived load zones with enough shared samples;
- overall `comparison_quality` with status `usable`, `partial`, or `unavailable` and setup scope.

Interpret all deltas as local minus reference. Give exact entry speed, minimum speed, peak-brake, brake-release, steering, and throttle-pickup targets only when `comparison_quality.status` is `usable` and the relevant signals exist. A load-zone label is not an official corner name unless a sourced map supplies that identity.

## Web bundle

When the seasonal bundle is missing, incomplete, stale, or invalid, gather current primary sources for the exact content variant. Archive facts, not undifferentiated page dumps. Include:

- official iRacing car manuals and setup guides;
- official track/layout facts and maps;
- official release notes relevant to physics, tires, or layout;
- official series/session rules and scheduled distance;
- source image/manual URL, title, retrieved timestamp, and local artifact/hash when downloaded.

Use telemetry coordinates for a deterministic track shape when possible. External images support orientation and corner naming; they do not replace telemetry alignment.

At the next iRacing season, use the new season key; its first lookup is normally `missing`, and the prior season's bundle remains archived. Refresh sooner after a relevant physics/content update. Store and reuse a stable sim/physics fingerprint made from relevant build, tire model, car physics, and track-content identifiers. Pass that same fingerprint during cache checks; a mismatch within the season marks the bundle `stale`. Do not include timestamps or unrelated volatile values.

A bundle is `fresh` only when its manifest and content hash validate and both facts and sources are non-empty. Garage61-only sync data is retained but leaves the bundle `incomplete` until the primary car/track research is archived. A second race in the same valid season/car/layout/setup bundle should require only race-specific enrichment.
