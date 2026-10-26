# HSLegendArriver Core Philosophy

> 必须时刻保持规划。每次修改前先判断：这个修改是否只是浮于表面？有没有更为根本的问题？优先解决根本问题，保持模块隔离、最小可用、接口清晰、小步验证。

## Permanent Rules

1. No code changes without a task book.
2. Read `TOTAL_TASK.md` and this file before starting or finishing any task.
3. Prefer root-cause fixes over local patches.
4. Keep domain, application, adapters, and bootstrap dependencies one-way.
5. Implement the minimum vertical slice that proves the interface.
6. Every behavior claim requires fresh verification evidence.
7. Use uv only: no pip, Conda, standalone venv, or system Python.
8. One task, one independently reviewable commit.
9. No direct pushes to `main` or `develop`.
10. Update interfaces and handoff state before committing.

## Garbage-Code Rule

When a task encounters dead code, duplicated logic, obsolete compatibility layers, or over-defensive branches inside its scope, refactor it now if the change is directly traceable to the current diagnosis and covered by tests. Do not preserve garbage merely to minimize the diff. If discovered outside the current task scope, record a follow-up task and do not mix it into the current commit.
