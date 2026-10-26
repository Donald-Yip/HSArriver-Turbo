# Decisions

## 2026-10-27 - Remote Naming

Ruling: Rename the personal remote to `origin` and the upstream repository to `upstream` - removes ambiguous `mine`/`origin` usage and establishes a standard push/pull boundary - if wrong, only local remote names need to be restored.

## 2026-10-27 - MVP Scope

Ruling: First official MVP is one complete game from the main menu through settlement, then stop - directly matches the approved user goal and prevents continuous-run features from expanding the first slice - if wrong, the runner can be invoked for a narrower starting state without changing domain contracts.

## 2026-10-27 - New Architecture Is Additive

Ruling: Build the new package alongside the legacy application and cut over only after one-game acceptance - lowest-risk migration and preserves the current fallback - if wrong, rollback is to ignore the new entrypoint and keep legacy active.

## 2026-10-27 - Test Rationalization

Ruling: Treat the existing test count as inventory, not value; keep behavior/contract/regression tests, replace source-text and implementation-detail tests, and move native tests to opt-in gates - reduces maintenance noise and makes CI trustworthy - if wrong, restore deleted tests from Git and document the missing behavior.

## 2026-10-27 - Garbage-Code Handling

Ruling: Refactor garbage code immediately only when it is inside the current task scope and traceable to a root-cause diagnosis; otherwise create a follow-up task instead of mixing unrelated cleanup into the current commit - keeps progress honest while preventing drift.

## 2026-10-27 - Split R02

Ruling: Split R02 into R02A inventory, R02B CI/architecture gates, and R02C test rationalization - the original task combined irreversible test deletion with gate creation and risked coverage loss - if wrong, the three sequential commits can be reverted independently without touching production code.

## 2026-10-27 - Split R02C Into Batches

Ruling: Execute test rationalization as R02C1-R02C5 by replacement batch - prevents a single irreversible test-cleanup commit and keeps each deletion traceable - if wrong, batches can be reverted independently.
