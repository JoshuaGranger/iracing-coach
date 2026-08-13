# Progressive Tuning overhaul acceptance matrix

This matrix is the binding acceptance surface for the representative-race, official-turn, structured-feedback, deterministic-candidate, and bounded-AI overhaul. Passing a source-string test alone does not satisfy visual or real-evidence acceptance.

| Area | Required evidence | Acceptance checks |
| --- | --- | --- |
| Exact selection | Exact analysis/session/configuration and setup identities survive race switching | Two same-car/same-track recordings never reuse one another's analysis; fixed evidence and open target remain distinct |
| Eligibility | Every exclusion has one human reason | Missing setup/fingerprint, fixed-only target, insufficient clean run, tow/repair, traffic, and unsupported car rules are independently exercised |
| Representative runs | Stable automatic ranking plus manual override | Longest/highest-quality clean green run wins; outlaps, pit/caution/incident/repair/traffic laps are excluded; phase lap IDs remain auditable |
| Turn catalog | Exact configuration, geometry hash, provenance, confidence, revision | Official/captured/manual/verified sources load; unknown stays unknown; changed geometry invalidates stale alignment |
| Track interaction | Whole-segment pointer and keyboard selection | Hover does not jitter or rerun analysis; wraparound turns highlight correctly; labels and hit areas work at 2/4/9/20 turns |
| Feedback | Early/Middle/Late stage, optional Entry/Center/Exit/Whole, multiple symptoms, severity/confidence/note | `Good` differs from skipped; multiple issues persist; generic note cannot create a candidate alone; every card contributes |
| Draft durability | Atomic exact-identity autosave | Navigate, close/reopen, switch races, restore, edit, and delete without cross-race leakage or duplicate canonical experiments |
| Evidence mapping | Driver feedback and measured/derived/proxy/predicted evidence remain distinct | Official turn bounds map by normalized-distance overlap; unavailable phases remain unavailable; no telemetry-only causality claim |
| Rule catalog | Versioned O'Reilly/Xfinity rules with verified source/setup path/range/constraints | Absent or locked fields produce no candidate; stale sim build invalidates the rule; other NASCAR families stay unavailable until verified |
| Candidate engine | Deterministic, repeatable, one primary logical system | Same input yields same evidence/candidate hash; conflicts are visible; legal/platform issues precede balance; prior failed candidates are suppressed |
| Numeric truth | Exact values require verified current value, step, range, and constraint headroom | Unknown range emits at most one-step/manual-confirmation language; no STO write occurs |
| AI boundary | Strict selected candidate/evidence IDs only | Offline/signed-out/interrupted/malformed/unknown-ID responses fall back; raw IBT, credential, and unrestricted filesystem paths never enter the prompt |
| Recommendation UX | One direct change, expected effect, tradeoff, affected turns, test plan, rollback | Result remains useful without AI and links back to the supporting map/telemetry evidence |
| Outcome | Subjective and matched-analysis result paths | Practice/test/qualifying/race result compatibility is checked; per-feedback outcome persists; incompatible analysis is rejected |
| Visual fit | 1280x720 and 1920x1080 browser, native restored/maximized, 100/150/200% scaling | Click every control; no clipping, overlap, horizontal page scroll, focus loss, motion desynchronization, or unreadable text |
| Accessibility | Keyboard, screen reader names, focus, high contrast, reduced motion | Every turn is reachable without the SVG; focus returns after editor close; essential state is never tooltip- or color-only |
| Performance | Cached page load, pointer hover, draft save, deterministic analysis | Pointer work stays within one frame; no backend call on hover; saves are nonblocking/atomic; ordinary tuning remains within product target |

## Required visual states

- Populated verified four-turn oval.
- Low-confidence turn map awaiting correction.
- No sourced turn annotations with truthful whole-lap fallback.
- Fixed evidence with compatible open target and fixed evidence without one.
- One complete turn, several partial turns, conflicting stages, and a long user note.
- Deterministic recommendation with AI offline, AI valid, AI invalid, no safe candidate, and unsupported car rules.
- Result comparison available, incompatible, subjective-only, and prior failed-experiment suppression.
- Empty, loading, saving, cancelled, backend error, reduced-motion, long label, and minimum-height states.

## Release boundary

The feature may be called implemented in development after focused backend, coordinator, UI, persistence, and contract suites pass. It may be called accepted only after the visual matrix is captured and reviewed and at least one clean O'Reilly/Xfinity open-setup A/B cycle is completed with a real representative recording. An unavailable official-turn source or unsupported car family remains a truthful product state, not permission to fabricate labels or reuse another ruleset.
