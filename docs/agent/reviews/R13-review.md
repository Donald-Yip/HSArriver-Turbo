# R13 Live Startup Focus Review

**Disposition: Approved**

## Findings

- PASS — Authorized `--allow-live` activation runs before readiness checks: focus, 0.35-second wait, then the same read-only `run_preflight`; the combined report includes the activation check first.
- PASS — Activation, probe, or readiness failure returns before native graph construction and `execute()`, so no game execution or click occurs. The focused no-native-call test covers this boundary.
- PASS — A passing live preflight emits no report output; the CLI later emits exactly one final JSON object. The success-path test parses the complete captured stdout as one JSON document.
- PASS — Fake mode skips live activation/preflight, explicit preflight remains read-only and rejects `--allow-live`, and missing `--allow-live` still fails before native construction.
- PASS — No regressions found in architecture, import isolation, typed reasons, canonical report output, Power.log validation, or UI evidence.

Verification: architecture gate PASS; focused tests 36/36; full suite 1819/1819; `compileall` and `git diff --check` PASS. No live game command was run; self-check is blocked only by the current process not being elevated.
