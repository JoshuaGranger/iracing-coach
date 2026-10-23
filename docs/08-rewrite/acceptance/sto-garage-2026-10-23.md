# Verified garage representations in the setup editor

The racing decision is to make a deliberate setup adjustment using the garage's language, then compare its effect in a later recording. The existing Setups editor shows Python-supplied garage labels, values and units where verified. Save continues to append an immutable revision; Save as forks an editable Coach setup from a locked imported original. Downloaded copies are checked in iRacing before being used for a run.

## Evidence and scope

The first profile requires the verified simulator material, an exact native car identity and the ordered 88-field layout signature. Its label identifies Supra garage verification. A source with the Camaro native header was loaded in the Supra, and a native Save as produced the Supra header with the same layout. This does not establish a separately tested Camaro garage or universal Xfinity adjustment ranges.

Controlled, offline, solo-garage adjustments and native Save as comparisons establish each enabled inverse and increment. Pressure uses the car's native float32 factor and six-decimal serialization, independently checked against native saves; substituting ideal SI conversion changes stored values. Suspension geometry is not converted from a plausible-looking raw angle or displacement. Unknown cars, layouts and fields retain native values. The registry contains field names, a value-free layout signature and evidence metadata; private setup content and crypto material remain outside Git.

Only measured bounds are supplied. A neighboring adjustment establishes a local increment, not an unobserved minimum or maximum. Geometry and ride-height dependent limits remain unverified. The garage's Tech result remains authoritative for the selected car, track and complete setup.

Final registry profile: `xfinity-2019-native:v7:ac810d10212166f97b1c`, descriptor version 2, label **Xfinity 2019 · verified in Supra**.

| Garage field | Unit | Verified adjustment metadata |
|---|---|---|
| Cold pressure, all four corners | psi | 0.5 step from zero; minimum/maximum unverified |
| Front brake bias | % | 0.5 percentage-point step from zero; minimum/maximum unverified |
| Steering ratio | :1 | Identity inverse; choices, step and limits unverified |
| LF LS compression | clicks | Minimum 1, maximum 10, step 1 from 1 |

Steering offset stays native-only: the third controlled point disproved the initial linear inverse model. No approximate inverse or garage degree claim was enabled.

## Safety and compatibility

`StoField` retains its native metadata and exact token, with an optional garage representation. The UI displays exact backend strings and performs no conversion, rounding or constraint math. Dedicated garage save endpoints prevent an older server from silently accepting a body it does not understand. Native-only Save, Save as, Restore and receipt hashes retain their released semantics.

Python validates changed fields on both write representations so a raw request cannot bypass a verified grid or bound. Numerically unchanged values preserve their original token and bytes, including existing values outside current constraints. Unknown fields, notes and native trailers retain the existing lossless behavior.

Pending requests retain their exact endpoint, representation, body, profile, descriptor version, If-Match and idempotency key through retry and reload. Version 1 pending records remain native requests. Dirty values whose mapping changes remain visible and cannot be saved under a new interpretation. Opening a historical revision refreshes its garage descriptors without rewriting its bytes or stored history. If decoding material is unavailable, a cached native view has garage descriptors removed.

## Validation record

The final Python mapping and writer have 65 focused synthetic tests. The server has 16 STO tests, including mixed writes, native constraint bypass prevention, exact retry across restart, compatibility with pre-garage receipts, and descriptor refresh/fallback without stored-history mutation. TypeScript checks and all 696 web unit tests passed. All 20 focused editor browser cases and 33 surrounding Setups cases passed across the editor workflows and responsive desktop/tablet/phone cases.

Two integration defects were corrected before release: blocked drafts no longer appear beside a new profile's unit or limits, and a successful Save as clears the unsaved-work state before navigation. Codex Sol 6.1 re-review approved both fixes with no remaining confirmed P1/P2 findings. Opus 5.5 at xhigh implemented the UI package; Codex owns integration and test results.

The actual Rust HTTP service with the real Python codec passed a seven-field garage Save as, decimal-equivalent byte-identical no-op, off-grid and out-of-range rejection, native bypass rejection, stale-profile rejection, exact idempotent retry, history append and hash-verified download. All other native fields and the copied source remained unchanged. A real browser against that service passed locked original, Save as, pressure/bias save, historical download, append-only Restore, desktop/phone overflow and browser-error checks using the final profile.

The final native load first caught a real writer defect: integer-spelled identity values were skipped by iRacing's floating-token parameter loader. The native writer format and loader state transition confirmed the cause. A controlled copy with identical numbers, notes and trailer but six-decimal spelling loaded the requested chassis values and passed Tech. The corrected real API produced exactly the same bytes, and equivalent legacy native integer requests produced byte-identical output. The shared Python writer now emits exact fixed decimals for changed values without rounding; existing numeric no-ops remain byte-identical.

Native Save as can normalize pressure storage by one float32 ULP while retaining the requested garage PSI. The legacy raw-pressure validation deliberately remains strict: an unchanged native token is always preserved, but a changed raw token outside the proven inverse spelling may be rejected even if it rounds to an on-grid display. Use the verified garage representation for pressure edits. No generic epsilon or rounded-display check was introduced to admit unsupported raw values.

The corrected native Save as comparison passed all seven garage targets, all 81 untouched parameter tokens, all 88 ordered names, notes, opaque trailer and car identity. Only two edited pressure tokens received the one-ULP normalization described above; their garage displays were unchanged. iRacing generated a new file key as expected.

The final read-policy guard omits garage descriptors for original tokens whose numeric syntax is not proven acceptable to the native loader and adds one warning. Native values, editability and source bytes remain intact. The actual failed test file correctly falls back for its three integer-spelled fields; the corrected file retains all seven mappings without that warning. This read-only policy change leaves the simulator-verified generated bytes identical.

Both complete gates passed on release commit `f025aa7a51975778a33966ae6ca4e0324ca6f91b`: `tools/check.ps1` and `tools/check.ps1 -Linux`. Linux ran 1,596 Python tests with 38 expected platform/private-corpus skips; Windows included repository verification, strict Rust format/lint/workspace tests, generated wire/theme drift, web typecheck, 696 unit tests and production build. Test-generated Windows cache leaves in the dedicated release checkout were preserved privately and its tracked cache files restored. Unrelated collector changes were excluded from the release.

The final Python-only read guard is commit `0ec36a802a9459f28f7b18dcde9142f5b97596fb`. Full Windows repository/Python verification passed all 1,599 tests (18 expected skips), and `tools/check.ps1 -Linux -LinuxScope python` passed all 1,599 tests (38 expected skips). Rust/UI code is unchanged from the complete gates above. Real HTTP and browser checks passed again with the final v7 profile, and the generated bytes remained identical to the native-tested file.

The native load-away/reload check passed: after loading another setup, reopening the native-resaved copy retained all seven requested garage values and Passed Tech. The corrected API file and native-tested decimal probe have identical SHA-256 `f1d222961093e400eaf601a7fc1c6e7757e84b14d866fb99c1a5dd4b46c2da9f`. Private screenshots and exact-value receipts remain outside Git.

Managed release of `0ec36a802a9459f28f7b18dcde9142f5b97596fb` completed under D14. The home builder took 153 seconds; `iracing-coach-app-1` is healthy at schema 5 and the archive reports `nas.state=ok`. No analysis/light job was active before or after release. The existing 322 deferred analyses are unchanged.

Read-only live desktop/phone checks passed: 1,364 imported occurrences, zero production working lineages, 88 native fields with seven verified garage mappings, locked originals, matching original download SHA, zero browser errors and no horizontal overflow. The imported malformed test copy correctly shows native fallback and one warning. A stateless invocation of the deployed Linux codec over stdin produced exactly the native-tested candidate SHA, without creating a production revision. The live UI is at `http://192.168.1.81:8210/#/d/1507810/setups`.

The prior Talladega setup was restored and Passed Tech. The owned simulator exited through its native Quit flow without saving a replay; window inventory confirmed the simulator was absent and the original iRacing UI was foreground. Lease release was acknowledged and remote status reported no lease. Follow-up setup writes were uniquely named test copies; purchased sources were not written. The follow-up hash-refresh job failed because the process API returned no usable executable path, so no fresh hash claim is made from that job; the prior successful source/executable hashes remain the existing controls. Private final receipts and screenshots remain outside Git.
