# Gaps, Contradictions, and Open Questions

This is the current criticism backlog. Items remain open until evidence closes them; they are not hidden in optimistic release prose.

| ID | Issue | Why it matters | Closure evidence |
| --- | --- | --- | --- |
| `GAP-001` | Real telemetry acceptance is HOME_QA pending. | Fixtures cannot prove SDK field behavior, timing, reconnects, or real session variance. | Passed HOME_QA report tied to exact build and representative sessions. |
| `GAP-002` | `CapabilityRegistry` reports Track Map and Setup Comparison as not implemented while related UI exists. | Capability truth can disagree with user-visible reality and automated gating. | Registry corrected/removed and tests aligned to the authoritative capability model. |
| `GAP-003` | Starting Tune is not a complete first-class desktop workflow. | A major intended coaching journey may require hidden/backend-only steps. | End-to-end UI, durable history, evidence, tests, and acceptance for a new event/car/track scenario. |
| `GAP-004` | Portable Garage61 credential behavior and security model need one explicit decision. | Seamless cross-PC copying conflicts with machine/account-bound encryption. | Threat-model decision, UI copy, implementation, migration behavior, and security tests. |
| `GAP-005` | Full grade-rubric calibration is not established against diverse real sessions. | Visually complete grades may imply precision beyond evidence. | Published rubric corpus, unavailable-category rules, expert review, and calibrated real-session results. |
| `GAP-006` | External Garage61 behavior is provider/account dependent. | Passing adapters do not prove current production endpoints or permissions. | Authorized integration acceptance with redacted request/response metadata and error cases. |
| `GAP-007` | Accessibility coverage is not exhaustive. | Keyboard, screen reader, scaling, contrast, and chart semantics can regress outside screenshot tests. | WCAG-oriented audit on packaged build with logged remediation. |
| `GAP-008` | Performance thresholds need broader representative libraries and hardware. | Small fixtures may hide import, chart, and list scaling defects. | Versioned benchmark corpus, hardware context, thresholds, and percentile results. |
| `GAP-009` | Requirement authority is distributed across several handoffs and corrective prompts. | Agents can implement obsolete prose or miss a superseding constraint. | Consolidated approved product specification and archived/superseded source labels. |
| `GAP-010` | Some source handoff Markdown contains mojibake. | Corrupted punctuation reduces review quality and can change literal assertions. | UTF-8 normalization of authoritative sources without semantic change. |
| `GAP-011` | The repository includes generated Codex protocol schemas and preview dependencies of uncertain long-term necessity. | Vendor/generated bulk complicates review, updates, and security inventory. | Dependency decision documenting generation source, update cadence, license, and whether files should be fetched at build. |
| `GAP-012` | Support for all owned/downloaded cars depends on available local/recorded metadata. | The planning selector must not invent ownership or present stale/non-owned cars as fact. | Defined local source hierarchy, freshness/error behavior, and real account/library acceptance. |

## Open product questions

1. What exact Windows versions, WebView2 versions, scaling factors, and minimum hardware define the support matrix?
2. Is cross-machine portability of external-service credentials mandatory, optional, or explicitly excluded?
3. What source is authoritative for owned cars when iRacing does not expose ownership locally in a reliable supported form?
4. What threshold of real-session coverage is required before removing the HOME_QA limitation?
5. Should Starting Tune be promoted to a distinct destination or remain within Progressive Tuning?
6. Which optional AI behaviors, if any, are release requirements rather than roadmap ideas?
7. How long should histories, logs, cached raw extracts, and backups be retained by default?

## Resolution rule

Closing an item requires a specification update, implementation change when applicable, and evidence. A comment such as "works locally" or a screenshot without scenario provenance is insufficient.
