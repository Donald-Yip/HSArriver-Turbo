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

## 2026-10-27 - R05 Window Tracker Scope

Ruling: R05 may modify `window_tracker.py` only for deterministic shutdown cleanup - the full headless gate exposed an unrelated timing flake, and the fix is isolated to lifecycle cleanup - if wrong, revert that hunk and the corresponding tracker test with the rest of R05.

## 2026-10-27 R02E Count Accounting

- Decision: cleanup count gates must report `default`, `discover`, and `opt-in` separately.
- Reason: `tests/application`, `tests/domain`, `tests/adapters`, and `tests/plugins` are not collected by the default top-level headless runner.
- Consequence: B2 reduces `discover` but not `default`; corrected final targets are default 1,495, discover 1,657, opt-in 20.

## 2026-10-27 R02E Gate Structure And Rollback

- Decision: deterministic integration modules live under `tests/opt_in`; real-window modules live under `tests/native_live`.
- Decision: default contracts inspect gate manifests by filename only and never import opt-in modules.
- Decision: O12 requires Git exit code exactly 1; fatal repository errors fail closed.
- Decision: MCP uses the real stdio smoke as its gate; no static source-structure replacement is added.
- Decision: B9, B8, and B7 must each reverse-apply cleanly from final HEAD.

## 2026-10-27 R02E Final Portfolio

- Decision: behavior gates replace source-string and private call-order assertions, not merely move them.
- Decision: frontend behavior is exercised through the real `web/index.html` script in Node with a fake DOM.
- Decision: overlay teardown is exercised through public `start()` lifecycle behavior with fake Tk, including a failed-mainloop path.
- Final bounds: default 1,495; discover 1,657; CI opt-in 19; native-live 1.
## 2026-10-27 R13 Split And Legacy Boundary

Ruling: Split native work into R13A offline anti-corruption bridge and R13 live smoke/cutover. Only modules under `hslegend.adapters.legacy` may import stable root/src legacy systems; application, bootstrap, plugins, and entrypoints remain legacy-free, and `FSM_action.py`, `manual_controller.py`, and `web_ui.py` remain forbidden. Reason: the adapter layer owns external-system translation while bootstrap owns composition; mixing those concerns would recreate the original god-object problem. Cost if wrong: the native bridge may need one additional adapter export, reversible within R13A.\n