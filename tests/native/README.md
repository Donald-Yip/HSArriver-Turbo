# Opt-in native and Node smoke tests

This directory is intentionally **not** a Python package and must not contain an
`__init__.py`. `python -m unittest discover -s tests -t .` therefore does not recurse
into these tests, and `tools/run_headless_tests.py` enumerates only top-level
`tests/test_*.py` modules rather than relying on post-discovery filtering.

Run the opt-in tests explicitly:

```powershell
uv run python -m unittest tests.native.test_process_enumeration -v
uv run python -m unittest tests.native.test_surrender_frontend -v
```

The Node test auto-discovers `node`/`node.exe` from `PATH` and standard
environment-derived Node.js installation directories. Set `HSL_NODE` to an explicit
executable when needed:

```powershell
$env:HSL_NODE = 'C:/Tools/nodejs/node.exe'
uv run python -m unittest tests.native.test_surrender_frontend -v
```

The frontend subprocess always decodes stdout and stderr as UTF-8. These tests are
smoke/integration gates, not part of the default headless suite.
