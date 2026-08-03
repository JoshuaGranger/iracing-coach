# Criticism model

The goal is not to produce agreement. The goal is to expose where English intent, executable contracts, implementation, tests, and user value diverge.

## Review dimensions

For each requirement, test:

1. **Clarity:** Can two implementers interpret it differently?
2. **Completeness:** Are inputs, absent-data behavior, and failure states defined?
3. **Consistency:** Does it conflict with another requirement, token, contract, or UI path?
4. **Feasibility:** Can available telemetry, permissions, and platform APIs support it?
5. **Truthfulness:** Could the required copy overstate evidence or causal certainty?
6. **User value:** Does it help a driver make a decision, or merely expose implementation state?
7. **Testability:** Is there an observable oracle rather than a subjective adjective?
8. **Reality:** Is the requirement implemented in production, only in fixtures, or not at all?
9. **Test adequacy:** Could the existing test pass while the user experience remains wrong?
10. **Operational risk:** Could it expose secrets, mutate source files, lose durable data, block the UI, or trigger costly requests?

## Finding classes

- `SPEC-AMBIGUITY`: multiple defensible readings.
- `SPEC-CONFLICT`: two normative sources disagree.
- `SPEC-IMPOSSIBLE`: evidence or permission cannot support the requested behavior.
- `REALITY-GAP`: implementation is absent or partial.
- `REALITY-DRIFT`: implementation contradicts current intent.
- `TEST-GAP`: no adequate oracle or state coverage.
- `TEST-FALSE-CONFIDENCE`: tests pass without proving user-facing behavior.
- `EVIDENCE-OVERCLAIM`: claim strength exceeds its evidence.
- `UX-FRICTION`: task succeeds but interaction is confusing or unnecessarily costly.
- `SECURITY-RISK`: secret, path, network, or source-data boundary is weakened.
- `OPERABILITY-RISK`: install, upgrade, recovery, diagnostics, or performance is inadequate.

## Severity

- **Critical:** secret exposure, source/durable-data loss, or unsafe destructive behavior.
- **High:** crash, core workflow failure, unsupported material claim, or install/upgrade failure.
- **Medium:** significant friction, incomplete evidence, inconsistent behavior, or material test gap.
- **Low:** polish, wording, maintainability, or narrow edge-case issue.

## Required finding evidence

Every finding must cite:

- requirement identifier or source;
- implementation/test file or direct observation;
- reproduction state;
- expected and actual result;
- user or safety impact;
- proposed resolution or explicit decision needed.

Use the [finding template](../07-agent-review/finding-template.md). Do not label a subjective preference as a defect without tying it to an approved product goal.
