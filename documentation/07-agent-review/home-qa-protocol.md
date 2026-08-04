# Retired Remote-QA Protocol

Status: retired on 2026-08-03 by Joshua. The former DEV/HOME_QA multi-PC coordination workflow no longer governs development, testing, or acceptance.

Current work is validated directly in the active development environment with evidence appropriate to the change: automated tests, native Windows interaction, deterministic replay, local recorded races, real SDK sessions, targeted performance measurements, and packaged lifecycle checks only when packaging or lifecycle behavior changes.

Historical snapshots and release records may still mention HOME_QA because that accurately describes the process used at the time. Agents MUST NOT create signals, wait for another PC, defer an otherwise available real-system check, or claim that a separate role owns acceptance.

When a real-system verdict matters, record the exact executable or commit, Windows and display context, simulator/source, car/track/session, timestamp, observed result, and sanitized evidence. Never commit raw private IBT files or secrets.
