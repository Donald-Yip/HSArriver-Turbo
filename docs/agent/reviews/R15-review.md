# R15 Review — Round 2

## Spec Compliance

- **PASS — Full pre-refactor UI preservation.** The staged change does not modify `web/index.html`, existing HTTP handlers, automation modules, coordinates, delays, thresholds, configuration schema, or domain logic. The `web_ui.py` diff is confined to early stdio configuration.
- **PASS — UTF-8 stdio fix.** `web_ui.py` reconfigures stdout and stderr to UTF-8 with replacement handling before localized startup output. A direct probe under the pinned `.venv` interpreter confirmed both streams report `('utf-8', 'replace')`; the opt-in test exercises `_configure_stdio()`.
- **PASS — Direct, elevated, and scheduled-launcher UTF-8 paths.** Direct `Popen` receives `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8`; elevated launch sets them in the environment before ShellExecute; the generated task launcher explicitly sets both variables.
- **PASS — Stale task is fail-closed in code.** `_task_state()` now parses the actual `<Command>` from the task XML, exposes `actual_action`/`expected_action`/`matches_current`, and `_serve_start_via_task` refuses to trigger a task whose action differs from the current launcher. A focused test confirms the trigger call is not made on mismatch.
- **PASS — Task status reports actual action.** Text and JSON status use `actual_action`, and text status directs the user to `install-task --force` when it differs.
- **PASS — Focused regression coverage exists.** Tests now cover direct child environment, elevated launch inheritance, generated launcher contents, stdio reconfiguration, and stale-action rejection.
- **PASS WITH EXTERNAL HOST ACTION — Host task remains stale.** The local `HSLegendArriver_serve` task still points to the Desktop launcher, and `hsctl serve task-status` correctly reports `matches_current: false`. Re-registering the task at highest privilege is an external user-elevated step; the code correctly detects and blocks this state rather than triggering it.

## Strengths

- The actual task action is read from Task Scheduler XML instead of being reported as the desired launcher path.
- Namespace-aware `ElementTree` parsing handles the normal task XML format, with a limited inline fallback for malformed XML.
- The stale-task gate is evaluated before any `schtasks /run` or PowerShell trigger, preventing accidental startup of the wrong repository copy.
- The generated launcher is deterministic CRLF text with explicit UTF-8 settings and the pinned interpreter.
- The UTF-8 stdio test initially failed because its probe replaced `sys.stdout`; the implementation itself passed the equivalent corrected probe without changes.

## Issues

No blocking code defects found in the scoped re-review.

- **Optional parser hardening:** `_task_action_from_xml` returns only the `<Command>` value and ignores `<Arguments>`. The managed task has no arguments, and the launcher itself is the security boundary, so this is not a correctness defect for R15.
- **External host state:** The installed task must still be rebound by the user in an elevated shell. The code gate intentionally fails closed until then; this is not grounds to reject the code change.

## Assessment

**Approved.** The previous blocking findings are addressed: stale task actions are detected from actual XML and rejected before trigger, status reports the real action, and focused direct/elevated/launcher/stdio tests exist. Host task rebinding remains an explicit elevated user step, and the code safely prevents triggering the stale task.