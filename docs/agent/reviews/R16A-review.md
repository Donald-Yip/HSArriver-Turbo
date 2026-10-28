# R16A Review

## Spec Compliance

- **PASS — Original 2026.10.27 patch preserved.** The 18 tracked files and three new source/test files from the original desktop working tree are represented in the staged sync. Fifteen files are byte-for-byte equal to the original post-patch tree; the remaining six differ only where R15 had already changed the same files, or by a trailing newline in one new test.
- **PASS — R15/refactor changes retained.** `web_ui.py` keeps the UTF-8 stdio setup, `click.py` keeps the fail-closed foreground/point checks, `README.md` and `docs/FEATURES.md` keep the uv workflow, and `tests/test_web_logs_wiring.py` keeps the R15 fake-Tk coverage. The R16A patch adds only the original 10.27 hunks on top.
- **PASS — Ancient-god/ability feature is complete.** Parser `USE_ABILITY` recognition, `choose_skill.py`, adapter validation, `UseAbilityAction`, click coordinates, FSM human-like handling, result verification, and legacy tests are present and connected.
- **PASS — Recommendation circuit breaker is synced.** Retry signatures, parse-failure deduplication, one-shot stuck warnings, no-repeat execution, recovery after recommendation changes, and OCR raw logging match the original patch and its tests.
- **PASS — `BE_566` inventory correction is valid.** `cards.json` gives `BE_566` the hand-target text “选择一张你的手牌洗入你的牌库…”, and `tests/test_friendly_hand_target_battlecries.py` now includes `("BE_566", "虫蛀缠蟒")`. The metadata inventory test passes.
- **PASS — Version and docs consistency.** `version.py` is `2026.10.27`, the newest `CHANGELOG.md` entry is 2026-10-27, and the synced README/FEATURES/changelog text is present. Older dated examples remain examples rather than conflicting current-version claims.
- **PASS — Staged scope.** `check_task_scope.py --task R16A --json` reports `ok: true`, `violations: []`. All 28 staged paths are allowed; no `src/hslegend/**`, `pyproject.toml`, `uv.lock`, `.github/**`, coordinates, or timing-default regressions are present.
- **PASS — Verification evidence confirmed.** Focused suite: 136 tests passed. Full headless gate: 1558 passed. Discovery: 1865 passed. Opt-in: 20 passed. Architecture gate: 54 files passed; self-test 23/23 passed.

## Strengths

- The sync preserves the original implementation instead of rewriting it into a new architecture, exactly as R16A requires.
- The ability path fails closed when a card or skill name has no recorded panel order, preventing guessed clicks.
- The `BE_566` correction is recorded both in the test inventory and the implementation/handoff reports, avoiding the stale 14-card assumption.
- The existing R15 UTF-8, click-safety, fake-Tk, and uv documentation changes survive the merge.
- Full default, discover, opt-in, architecture, focused, and scope gates all pass.

## Issues

No blocking issues found.

- **Non-blocking line-ending note:** plain `git diff --cached --check` reports CR-at-EOL as trailing whitespace in `version.py` and `web_ui.py`; with the repository-appropriate `-c core.whitespace=cr-at-eol` setting the check passes. This is a CRLF repository artifact, not an extra source change, and the implementer report documents it.
- **No stale `BE_566` test assumption remains:** the inventory count and expected metadata now agree with the updated card table.

## Assessment

**Approved.** The final package faithfully imports the desktop 2026.10.27 patch, preserves R15/refactor behavior, validates the `BE_566` hand-target correction, stays within R16A scope, and is supported by passing focused, full, opt-in, architecture, and scope evidence.