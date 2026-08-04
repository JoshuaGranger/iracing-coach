# Gaps, Contradictions, and Open Questions

This is the current criticism backlog. Items remain open until evidence closes them; they are not hidden in optimistic release prose.

| ID | Issue | Why it matters | Closure evidence |
| --- | --- | --- | --- |
| `GAP-001` | Representative real-telemetry breadth remains incomplete. | Fixtures cannot prove every SDK field combination, timing, reconnect, car, or session variance. | Direct real-system evidence tied to exact builds and representative sessions, with failures retained rather than generalized away. |
| `GAP-002` | Closed in 0.11.0: the unused Setup Library surface was removed; internal setup indexing, recorded Track View, and Starting Tune remain aligned with capability truth and tests. | Capability truth must agree with user-visible reality and automated gating. | Reopen only with a named source/registry mismatch. |
| `GAP-003` | Closed in development in 0.11.0; packaged and real baseline acceptance remain quality gates rather than missing product behavior. | Starting Tune is first-class inside Setups. | Event/source/checks/run UI, package mapper, Q/R separation, and product-truth tests. |
| `GAP-004` | Machine-bound Garage61 credential policy is decided; implementation evidence is incomplete for the new migration/redaction/permissions matrix. | The destination PC must restore nonsecret state while requiring reconnection and never carrying a portable secret. | Migration, legacy-secret removal, redaction, and least-privilege storage tests tied to the packaged build. |
| `GAP-005` | Full grade-rubric calibration is not established against diverse real sessions. | Visually complete grades may imply precision beyond evidence. | Published rubric corpus, unavailable-category rules, expert review, and calibrated real-session results. |
| `GAP-006` | External Garage61 behavior is provider/account dependent. | Passing adapters do not prove current production endpoints or permissions. | Authorized integration acceptance with redacted request/response metadata and error cases. |
| `GAP-007` | Accessibility coverage is not exhaustive. | Keyboard, screen reader, scaling, contrast, and chart semantics can regress outside screenshot tests. | WCAG-oriented audit on packaged build with logged remediation. |
| `GAP-008` | Performance thresholds need broader representative libraries and hardware. | Small fixtures may hide import, chart, and list scaling defects. | Versioned benchmark corpus, hardware context, thresholds, and percentile results. |
| `GAP-009` | Requirement authority is distributed across several handoffs and corrective prompts. | Agents can implement obsolete prose or miss a superseding constraint. | Consolidated approved product specification and archived/superseded source labels. |
| `GAP-010` | Closed as stale/unreproducible on 2026-08-03. Current authoritative Markdown, JSON, C#, Razor, and XAML contain no mojibake marker sequences under a repository-wide scan. | Preserving a third-party rendering/export defect as a source gap would misstate repository reality. | Reopen only with an exact current file, byte sequence, and semantic impact. |
| `GAP-011` | The repository includes generated Codex protocol schemas and preview dependencies of uncertain long-term necessity. | Vendor/generated bulk complicates review, updates, and security inventory. | Dependency decision documenting generation source, update cadence, license, and whether files should be fetched at build. |
| `GAP-012` | Support for all owned/downloaded cars depends on available local/recorded metadata. | The planning selector must not invent ownership or present stale/non-owned cars as fact. | Defined local source hierarchy, freshness/error behavior, and real account/library acceptance. |

## Open product questions

1. What exact Windows versions, WebView2 versions, scaling factors, and minimum hardware define the support matrix?
2. External-service credentials are explicitly excluded from portable migration and require reconnection on the destination PC.
3. What source is authoritative for owned cars when iRacing does not expose ownership locally in a reliable supported form?
4. What threshold of real-session coverage is required for each telemetry-related release claim?
5. Starting Tune remains within Setups; Progressive Tuning remains event-linked follow-up unless user research justifies a navigation change.
6. Which optional AI behaviors, if any, are release requirements rather than roadmap ideas?
7. How long should histories, logs, cached raw extracts, and backups be retained by default?

## Resolution rule

Closing an item requires a specification update, implementation change when applicable, and evidence. A comment such as "works locally" or a screenshot without scenario provenance is insufficient.
