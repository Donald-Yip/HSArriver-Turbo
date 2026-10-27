# Explicit Integration Gates

These directories are intentionally not Python packages and must not contain an
`__init__.py`, so ordinary discovery does not import them.

- `tests/opt_in`: deterministic Node/native/tooling checks run by Windows CI.
- `tests/native_live`: checks requiring a real Hearthstone window; never run by ordinary CI.

```powershell
uv run python tools/run_headless_tests.py --opt-in
uv run python tools/run_headless_tests.py --native-live
```

A skipped test in either explicit mode is a failure. Set `HSL_NODE` only when Node is
not discoverable from `PATH`.
