# Change Impact Checklist

Use this before accepting a material product, data, integration, or release change.

## Product and language

- Which stable requirement IDs change, become obsolete, or need to be added?
- Does the change conflict with a higher-authority product or safety source?
- Are visible labels and messages human, actionable, and truthful in every state?
- Does the navigation remain limited to real first-class capabilities?
- Are empty, unavailable, stale, partial, and failure states specified?

## Evidence and data

- Which recorded fields, calculations, inferences, comparisons, and user statements are involved?
- Can missing data be confused with zero or success?
- Are provenance, confidence, exclusions, and comparison fitness retained?
- Do identifiers and cache keys remain stable?
- Is a durable schema migration required, and can it roll back?
- Does portability or retention behavior change?

## Architecture and security

- Which process, contract, component, file root, or external service boundary changes?
- Does local/offline behavior still work?
- Are new writes constrained, validated, atomic, and logged without secrets?
- Are credentials, paths, telemetry, or setup contents newly exposed?
- Are dependency source, version, license, integrity, and update cadence documented?

## UI and accessibility

- Does every control have a real action, enabled/disabled reason, keyboard path, focus state, and accessible name?
- Have minimum size, scaling, long text, scrolling, contrast, chart semantics, and color-independent status been checked?
- Is healthy background activity quiet?
- Are destructive/external/long-running actions clear and recoverable?

## Verification and release

- Which .NET, Python, contract, fixture, visual, performance, installer, and privacy tests must change?
- Does the change require real telemetry, real Garage61, or HOME_QA evidence?
- Was the packaged executable tested rather than only the development host?
- Are release notes, compatibility contract, hashes, traceability matrix, and implementation snapshot updated?
- Is the source repository still free of credentials, personal data, raw telemetry, user setups, and oversized generated payloads?

## Completion rule

A change is complete only when the English requirement, implemented behavior, verification evidence, and current reality record agree or when an explicit documented limitation explains why they do not.
