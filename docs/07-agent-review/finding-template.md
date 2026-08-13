# Finding Template

Copy this template for each specification, reality, evidence, or change-impact finding.

```markdown
## [FINDING-ID] Concise outcome-oriented title

- Severity: blocker | critical | major | moderate | minor
- Class: specification | implementation | evidence | drift | security | accessibility | performance | release
- Requirement IDs: `...`
- Reviewed commit/build: `...`
- Environment/source: `...`
- Status: open | disputed | accepted | fixed | verified

### Observed fact

Describe exactly what was read or observed. Include the smallest useful reproduction and concrete source/test/UI locations.

### Expected behavior

State the governing requirement. If sources conflict, identify the conflict rather than silently choosing one.

### Impact

Explain the user, data-integrity, security, accessibility, operability, or release consequence.

### Evidence and counterevidence

- Supporting evidence: ...
- Counterevidence: ...
- Unknown/untested: ...

### Recommended correction

Describe the smallest coherent correction. Say whether English specification, implementation, tests, migration, release notes, or multiple layers must change.

### Verification

List the exact automated and manual checks required to close the finding. A fix is not verified until these pass on the intended artifact.
```

## Writing rules

- Use filenames, line numbers, screenshots, hashes, event IDs, and command results where they materially support the claim.
- Never include secrets, full credentials, authorization headers, or raw personal telemetry.
- Prefer one independently fixable problem per finding.
- Do not prescribe a code change when the defect is actually unresolved product policy.
- If severity depends on an assumption, state the assumption.
