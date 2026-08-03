# Authority and normative language

## Two independent authorities

Reviewers must answer two separate questions.

### What should the product do?

The authority order for intended behavior is:

1. Joshua's later explicit decisions.
2. The 2026-08-03 final-product completion specification recorded in the [source register](source-register.md).
3. This normalized documentation tree.
4. `companion-app-handoff/NEXT_DEVELOPMENT_PROMPT.md`.
5. Older handoff prose and release notes.

### What does the product actually do?

The authority order for functional reality is:

1. Direct observation of the exact installed artifact.
2. Executable contract and integration tests.
3. Current production source.
4. Machine-readable contracts and fixtures.
5. Release evidence.
6. Prose descriptions.

Code never silently amends product intent. Product prose never proves implementation.

## Normative words

- **MUST / MUST NOT:** release-blocking behavior or invariant.
- **SHOULD / SHOULD NOT:** expected behavior; deviation requires a recorded reason and evidence.
- **MAY:** permitted behavior that must not weaken a MUST.
- **Target:** a performance or quality objective. Missing a target is a finding; crossing a stated hard ceiling is a release failure.

## Requirement construction

Each normative requirement should identify:

1. the actor or component;
2. the triggering state;
3. the observable result;
4. the evidence or permission gate;
5. the failure behavior;
6. an acceptance oracle.

Avoid terms such as “professional,” “fast,” “useful,” “perfect,” or “supported” without an observable definition.

## Evidence boundaries

- Local fixture evidence can prove deterministic mapping, rendering, interaction, and failure containment.
- Fixture evidence cannot prove real iRacing SDK compatibility, real HOME_QA performance, Garage61 account scope, or live race usefulness.
- A screenshot proves one rendered state, not keyboard behavior, data lineage, request count, or correct calculations.
- A unit test proves the encoded case, not the completeness or desirability of the requirement.
- Only HOME_QA may mark a release **Accepted**.

## Change control

- Keep requirement identifiers stable.
- Record a changed requirement in the relevant document and update the traceability matrix.
- Do not convert a missing feature into “Unavailable” merely to satisfy a visual assertion.
- Do not convert implementation behavior into a specification without examining whether the behavior is desirable.
- When sources conflict, record the conflict in `06-reality/gaps-contradictions-and-open-questions.md`.
- Update `06-reality/implementation-snapshot-<version>.md` for every release candidate; do not rewrite older snapshots.
