# Agent Review Protocol

This protocol is the primary consumer contract for the documentation tree. Its goal is adversarial but constructive comparison of English requirements, source behavior, packaged behavior, and evidence.

## Review modes

1. **Specification criticism:** find ambiguity, contradiction, unverifiable language, missing states, unsafe assumptions, or requirements that cannot be implemented as written.
2. **Reality criticism:** find behavior that violates, incompletely realizes, or misleadingly appears to satisfy a requirement.
3. **Evidence criticism:** find tests or artifacts that do not actually establish the claim attached to them.
4. **Change criticism:** identify the requirements, data migrations, integrations, tests, UI states, and release evidence affected by a proposed change.

## Required reading order

1. `../00-governance/authority-and-language.md`
2. `../00-governance/source-register.md`
3. `../01-product/mission-scope-and-operating-context.md`
4. The applicable workflow, data, architecture, and quality documents
5. `../06-reality/implementation-snapshot-0.10.0.md`
6. `../06-reality/traceability-matrix.md`
7. `../06-reality/gaps-contradictions-and-open-questions.md`
8. Relevant source, tests, release packet, and packaged application

## Method

1. State the exact reviewed commit/build and documentation snapshot.
2. Select requirement IDs rather than reviewing a page by impression alone.
3. Quote or paraphrase the requirement narrowly; do not rewrite it during evaluation.
4. Inspect implementation and test anchors independently.
5. Exercise the packaged behavior where the claim is user-visible.
6. Test normal, empty, missing, stale, malformed, disconnected, partial, long-value, keyboard, and recovery states as applicable.
7. Classify the finding using `../00-governance/criticism-model.md`.
8. Separate observed fact, inference, impact, and proposed correction.
9. Record counterevidence and uncertainty. Do not inflate severity to compensate for incomplete inspection.
10. Update the traceability and reality snapshot when a finding changes the accepted state.

## Anti-patterns

- Treating source presence as proof that the executable reaches the code.
- Treating a screenshot as proof of interaction, data correctness, accessibility, or recovery.
- Treating a fixture as real telemetry or a mocked service as current provider acceptance.
- Changing a requirement to match a defect without an explicit product decision.
- Marking a requirement complete because the button exists.
- Reporting wording preference as a functional defect without tying it to ambiguity, comprehension, trust, or task completion.
- Reading secrets, personal IBTs, or unrelated user files merely because the desktop process has access.

## Minimum output

Each review produces: scope, environment, findings ordered by severity, verified conformances, untested areas, evidence links, and recommended next action. Use `finding-template.md` for each issue.
