# R06 Data Adapters

## Task ID

R06

## Goal

Implement the local data adapters required by the MVP: configuration, card catalog, run journal, and artifact storage.

## Scope

- `JsonConfigRepository` with atomic write, unknown-key preservation, deep merge, and typed `ConfigSnapshot`.
- `JsonCardCatalog` with one load per process, deterministic card IDs, mechanics/capabilities, and version/hash.
- `JsonlRunJournal` appending immutable execution records as one JSON object per line.
- `FileArtifactStore` with a constrained root, atomic writes, and path traversal rejection.
- Tests using temporary directories only.

## Non-Goals

- No OCR, capture, Power.log, click, or UI adapters.
- No legacy runtime modification.
- No database or network.

## Allowed Files

- `src/hslegend/adapters/config/**`
- `src/hslegend/adapters/cards/**`
- `src/hslegend/adapters/journal/**`
- `src/hslegend/adapters/artifacts/**`
- `tests/adapters/**`
- `docs/interfaces.md`
- `docs/architecture/**`
- `docs/tasks/R06-*.md`
- `docs/agent/**`
- `TOTAL_TASK.md`

## Forbidden Files

- Legacy root runtime modules.
- `src/hslegend/domain/**` and `src/hslegend/application/**` except approved bug fix.
- `cards.json` and user configuration files.

## Acceptance

- Config missing/corrupt/unknown-key/concurrent write behavior is deterministic.
- Card catalog returns identical definitions/capabilities for repeated lookups.
- Journal records are valid JSONL and recoverable after restart.
- ArtifactStore rejects absolute paths and `..` escapes.
- All adapters satisfy application port protocols.
- Headless, architecture, scope, domain/application gates pass.

## Risk And Rollback

- Risk: config concurrency and card metadata variability.
- Mitigation: sidecar lock/atomic replace, a single catalog instance, and read-only cards source.
- Rollback: delete new adapters/tests; no user data migration.

## Git Requirement

```text
feat(R06): add config, card, journal, and artifact adapters
```

## Report Paths

- Implementer: `docs/agent/reports/R06-implementer.md`
- Reviewer: `docs/agent/reviews/R06-review.md`
