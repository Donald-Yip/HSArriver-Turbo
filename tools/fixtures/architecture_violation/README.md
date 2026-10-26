# Architecture Violation Fixture

Run from the repository root:

```powershell
uv run python tools/check_architecture.py --root tools/fixtures/architecture_violation
```

It must exit nonzero with `domain_external_import`. This directory is never part
of the default scan, which only reads `src/hslegend` under the selected root.
