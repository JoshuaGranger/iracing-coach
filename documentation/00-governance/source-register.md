# Source register

This register records the material used to normalize the product specification. Hashes identify the exact inputs reviewed for the 0.9.3 documentation baseline.

| Source | Role | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| `companion-app-handoff/NEXT_DEVELOPMENT_PROMPT.md` | Later handoff product decisions | 47,501 | `86025856f48181eef979ccc2980f48aec3c0dcb04e209a65a79f54321005a89f` |
| `companion-app-handoff/START_HERE.md` | Required-reading order and contract precedence | 5,835 | `26be3a02aafbdd46e0da1c5cdbda54984729de84b800670fe48404a8867f2559` |
| `companion-app-handoff/BUILD_SPEC.md` | Original architecture and workflow contract | 8,834 | `da87c486efb5d4412bd4b89c9d85074e76b44d1a196239c6b99b8a96b0cf4419` |
| `companion-app-handoff/UI_DESIGN_SYSTEM.md` | Visual and accessibility contract | 9,053 | `e613affb2dc51dd37ba6069e650dca5a6559d3faade5f1191782aebbce51b18a` |
| `companion-app-handoff/BACKEND_INTEGRATION.md` | MCP/CLI integration contract | 6,739 | `1b2d7fb4f086f5357a3aff66781a436ab8ebbb0e3e78e318ddfd8b1fcb565d05` |
| `companion-app-handoff/AI_ORCHESTRATION.md` | Optional AI boundary | 4,433 | `12aaae10df2ca4a11247793232a52fe6c7b581a035f80cfb26244bf5d48ab049` |
| `companion-app-handoff/SECURITY_AND_TRANSFER.md` | Security and deployment boundary | 4,349 | `1a309b9c900479fa0cbbb45273ffbc697fb65d22349768fac92f1254ffc93d5b` |
| `companion-app-handoff/ACCEPTANCE_CHECKLIST.md` | Original release gate | 6,977 | `1db4b1f3134badd6de7c6ba27fbcc53c0f27ca2f51a86f35112e8466e4373ced` |
| External `FINAL_PRODUCT_COMPLETION_SPEC.md` | Joshua's 2026-08-03 end-state authority | 24,874 | `30e8cd8c17c919cd60b9c757e9acef7e7e6f854e1fd6adb1c5c6b651bf58ecd2` |
| External `expected-ui-assertions.json` | Machine-checkable UI expectations | 1,587 | `f7656cc4ac3cb19edbe2baeff484024c5b8b1bb47753722e5db82c26b4828c77` |
| `companion-app/RELEASE_0.9.3.md` | Local implementation and verification claim | 4,612 | `229b52fe9e0f36099eaf0df2d4b885c657a2fd3a5aba4b86f4e099dbb424a717` |

## Executable reality sources

- `companion-app/src/` — desktop source.
- `companion-app/tests/iRacingCoach.Tests/` — desktop contract and coordinator tests.
- `iracing-coach/skills/analyze-iracing-race/scripts/` — deterministic backend.
- `iracing-coach/tests/` — backend behavioral tests.
- `companion-app-handoff/contracts/` — compatibility and MCP snapshots.
- `companion-app/artifacts/qa/v0.9.3/` — local fixture walkthrough evidence; intentionally ignored by Git.

## Provenance limitation

The external final-product packet lived on a temporary coordination share and is not itself the durable specification. This documentation normalizes its requirements. A reviewer who receives the original packet can compare its recorded hash to this register.
