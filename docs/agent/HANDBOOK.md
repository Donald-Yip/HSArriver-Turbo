# Agent Handbook

## Reading Order

Every controller, implementer, or reviewer starts with:

1. `CORE_PHILOSOPHY.md`
2. `TOTAL_TASK.md`
3. `docs/agent/HANDBOOK.md`
4. The current task book in `docs/tasks/`
5. Relevant sections of `docs/interfaces.md`
6. `docs/agent/HANDOFF.md`

Do not request or read the whole legacy repository unless the current task explicitly allows it.

## Roles

### Main Controller

- Owns global plan, sequencing, rulings, Git, CI, and final integration.
- Dispatches one implementer and one independent reviewer per task.
- Verifies reports, diffs, tests, changed-file scope, and commit state.
- Sole writer of `TOTAL_TASK.md`, `TASK_BOARD.md`, `TASK_BOARD.json`, `HANDOFF.md`, and `DECISIONS.md`.
- Does not accept an implementer claim without fresh evidence.

### Implementer

- Implements one task only.
- Reads only the task book, interface contract, allowed files, and tests.
- Writes or updates its own report under `docs/agent/reports/`.
- Does not edit controller-owned files.
- Does not push.

### Reviewer

- Fresh context for every task.
- Reviews task compliance, architecture boundaries, tests, scope, and regression risk.
- Writes only its review file under `docs/agent/reviews/`.
- Does not modify production code.

## Task Lifecycle

```text
PLANNED -> READY -> IMPLEMENTING -> IMPLEMENTED
        -> REVIEWING -> CHANGES/APPROVED -> CI -> COMMITTED -> DONE
```

Maximum three fix rounds. A third failed round means the task must be re-scoped or redesigned.\n\nCanonical statuses: `planned`, `in_progress`, `ready_for_review`, `changes_requested`, `approved`, `committing`, `done`, `blocked`.

## Minimal Task Packet

Each implementer receives:

- Task ID and task book path.
- Goal, scope, non-goals.
- Exact interfaces consumed and produced.
- Allowed and forbidden files.
- Acceptance commands.
- Base SHA and branch.
- Report path.
- Stop conditions.

If the implementer needs global repository knowledge, the module boundary is not ready.

## Required Report Format

Every implementer report contains:

- Status.
- Base/head SHA and branch.
- Changed files.
- Interfaces produced/consumed.
- Verification commands and raw results, including exit codes and pass/fail/skip counts.
- Scope check.
- New dependencies and public API changes.
- Known risks and requested review focus.

Every reviewer report contains:

- Verdict: `APPROVED` or `CHANGES_REQUESTED`.
- Critical, important, and minor findings.
- Spec coverage.
- Architecture/dependency check.
- Test adequacy.
- Scope drift check.
- Rollback adequacy.

## Drift Stops

Stop and re-plan when:

- A file outside the allowed list changes.
- The task requires reading the whole legacy FSM/Web.
- The same test fails twice.
- Three review rounds fail.
- A new runtime dependency or public API change is needed.
- More than eight core files must change.
- Two tasks need to modify the same file concurrently.
- An interface contract proves unusable.

## Rulings

Controller decisions use this format in `DECISIONS.md`:

```text
Ruling: <decision> — <reason> — <cost if wrong>
```

Only ask the user for irreversible/destructive actions, security-sensitive actions, external/shared side effects such as push/merge/publish, or a genuinely broken plan.

## WIP Limit

- Default: one implementer and one reviewer.
- Parallel implementers are allowed only for disjoint files and stable interfaces.

## Verification

No completion claim without a fresh command and observed output. A subagent report is never sufficient by itself; the controller checks the diff and reruns the required gate.\n\nA commit cannot embed its own SHA. Reports use the base SHA plus the final commit discovered by `git log --grep "(Rxx)"`. After the commit, the controller records that SHA in the next task handoff.

## Required Checks And PR Flow

Every task report must include the raw commands, exit codes, and pass/fail/skip
counts for these checks (or a task-book-approved reason why a check does not
apply):

```powershell
uv sync --locked --all-groups
uv run python -m compileall -q src tests tools
uv run python tools/check_architecture.py
uv run python tools/check_architecture.py --self-test
uv run python tools/check_task_scope.py --task <TASK>
uv run python tools/check_task_scope.py --self-test
uv run python tools/run_headless_tests.py
```

The architecture gate is an AST-only dependency-direction and cycle check. The
scope gate reads `docs/tasks/<TASK>.md` and compares the staged diff by default;
CI uses the pull-request base SHA, push-before SHA, or `HEAD^` for dispatch, and
fails closed when the task ID or base ref cannot be resolved. The headless runner
excludes the Node frontend test and the two real-process enumeration tests until
R02C moves them.

Before opening a PR, fill `.github/pull_request_template.md` with the task ID,
task book, why, scope, interfaces, raw test results, architecture impact,
breaking-change status, risk/rollback, and checklist. A PR is not reviewable
without those fields. The controller remains the only role allowed to commit,
push, merge, or change task-board state.

## Git

- One task produces one final squash commit.
- Allowed types: `feat`, `fix`, `refactor`, `test`, `docs`, `build`, `ci`, `chore`, `release`.
- Commit subject uses the canonical `type(Rxx): summary` format.
- No direct push to `main` or `develop`.

## Garbage-Code Escalation

- Refactor immediately when garbage code is inside the current task's allowed scope, directly supports the task's root-cause diagnosis, and can be covered by current tests.
- Never perform unrelated cleanup in the same commit.
- Record out-of-scope discoveries as a new task in `TASK_BOARD` with an owner, priority, and evidence.
