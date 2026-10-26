# Dependency Rules

## Allowed

```text
domain <- application <- adapters
             ^
          plugins
bootstrap -> application + adapters + plugins
entrypoints -> bootstrap + application commands
```

## Forbidden

- Domain importing application, adapters, bootstrap, legacy modules, or external systems.
- Application importing adapters, bootstrap, entrypoints, `web_ui`, `FSM_action`, `manual_controller`, or `click`.
- Adapters importing use cases, bootstrap, or entrypoints.
- Plugins importing adapters, bootstrap, runtime, filesystem, network, or input backends.
- New modules importing root facades.

## Import-Time Rules

- No file reads at import time.
- No thread creation at import time.
- No global mutable runtime objects at import time.
- Configuration is supplied through ports.

## Verification

`tools/check_architecture.py` parses imports with the standard library AST. It must report:

- zero forbidden imports;
- zero cycles in the new package;
- zero plugin side-effect imports;
- zero domain imports outside the standard library and domain package.
